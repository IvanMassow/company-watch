"""
Company Watch - Smart Trader
Makes trading decisions based on report analysis and autonomous DD.
Key capabilities:
  - Process new reports (primary daily input)
  - Autonomous DD: check if thesis still holds between reports
  - Override logic: "human in the loop" AI that can override report advice
  - Profit-taking & stop-loss logic
  - Position management (BUY/SELL/HOLD/FADE state machine)
  - Pre-market DD: check HK tape and overnight news before NYSE open
  - Duck-and-cover: sell at open, re-buy later if storm incoming
  - Dual confidence: report_confidence vs house_confidence
"""
import json
import logging
import time
from datetime import datetime, timezone

from config import (
    WATCHED_TICKER, CONFIDENCE_ACT, CONFIDENCE_WATCH,
    PROFIT_TAKE_PCT, PROFIT_TAKE_STRONG_PCT,
    LOSS_STOP_PCT, LOSS_STOP_HARD_PCT,
    DRAWDOWN_FROM_PEAK_PCT,
    OVERRIDE_PRICE_MOVE_PCT, OVERRIDE_MARKET_CRASH_PCT,
    AV_RATE_LIMIT,
    DUCK_COVER_ENABLED, DUCK_SELL_MINUTES_AFTER_OPEN, DUCK_REBUY_MINUTES_AFTER_OPEN,
    DUCK_MIN_CONFIDENCE,
    PREMARKET_DD_ENABLED, PREMARKET_WINDOW_HOURS_BEFORE_OPEN,
    MARKET_OPEN_UTC, MARKET_CLOSE_UTC, MARKET_DAYS,
)
from db import get_db, get_current_position, get_passive_position, get_latest_report
from tracker import fetch_price_av, fetch_spy_price, calculate_pnl

logger = logging.getLogger("companywatch.trader")


def get_or_create_position(ticker):
    """Get current position or create a FLAT one."""
    pos = get_current_position(ticker)
    if pos:
        return pos

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO active_positions (ticker, state, direction, current_stance, stance_updated_at)
           VALUES (?, 'FLAT', NULL, 'FADE', ?)""",
        (ticker, now)
    )
    conn.commit()
    conn.close()
    return get_current_position(ticker)


def log_decision(ticker, decision_type, trigger, report_id, old_stance, new_stance,
                 confidence, reason, price, spy_price=None, spy_change=None,
                 market_regime=None, dd_type=None, dd_details=None,
                 llm_analysis=None, is_override=False, override_what=None,
                 report_confidence=None, house_confidence=None):
    """Log a trading decision to the decision_log table."""
    conn = get_db()
    conn.execute("""
        INSERT INTO decision_log
        (ticker, decision_type, trigger, report_id, old_stance, new_stance,
         confidence, report_confidence, house_confidence, reason,
         price_at_decision, spy_price, spy_change_pct,
         market_regime, dd_type, dd_details, llm_analysis,
         is_override, override_what)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker, decision_type, trigger, report_id, old_stance, new_stance,
        confidence, report_confidence, house_confidence, reason,
        price, spy_price, spy_change,
        market_regime, dd_type, dd_details, llm_analysis,
        1 if is_override else 0, override_what,
    ))
    conn.commit()
    conn.close()


def enter_position(ticker, direction, price, reason, report_id=None, confidence=None,
                   report_conf=None, house_conf=None):
    """Enter a new position (BUY or SHORT)."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    pos = get_current_position(ticker)

    if pos and pos['state'] != 'FLAT':
        # Close existing position first
        exit_position(ticker, price, "Reversing: " + reason, report_id)

    stance = 'BUY' if direction == 'LONG' else 'SELL'

    conn.execute("""
        INSERT INTO active_positions
        (ticker, state, direction, entry_price, entry_time, entry_reason,
         entry_report_id, current_stance, stance_confidence,
         report_confidence, house_confidence,
         stance_updated_at, stance_report_id, peak_price, trough_price)
        VALUES (?, 'HELD', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker, direction, price, now, reason,
        report_id, stance, confidence,
        report_conf, house_conf,
        now, report_id, price, price,
    ))
    conn.commit()
    conn.close()

    log_decision(
        ticker, 'ENTRY', 'report' if report_id else 'autonomous',
        report_id, 'FLAT', stance, confidence, reason, price,
        report_confidence=report_conf, house_confidence=house_conf,
    )
    logger.info("ENTERED %s %s @ $%.2f: %s", direction, ticker, price, reason)


def exit_position(ticker, price, reason, report_id=None):
    """Exit current position (sell if long, cover if short)."""
    pos = get_current_position(ticker)
    if not pos or pos['state'] == 'FLAT':
        logger.warning("No position to exit for %s", ticker)
        return

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Calculate realised P&L
    direction = pos.get('direction', 'LONG')
    pnl = calculate_pnl(pos['entry_price'], price, direction)

    old_stance = pos.get('current_stance', 'HOLD')

    conn.execute("""
        UPDATE active_positions SET
            state='FLAT', exit_price=?, exit_time=?, exit_reason=?,
            exit_report_id=?, current_stance='FADE', stance_confidence=0,
            stance_updated_at=?, realised_pnl_pct=?, closed_at=?
        WHERE id=?
    """, (
        price, now, reason, report_id, now, pnl, now, pos['id']
    ))
    conn.commit()
    conn.close()

    log_decision(
        ticker, 'EXIT', 'report' if report_id else 'autonomous',
        report_id, old_stance, 'FADE', 0, reason, price
    )
    logger.info(
        "EXITED %s %s @ $%.2f (P&L: %.2f%%): %s",
        direction, ticker, price, pnl, reason
    )


def update_stance(ticker, stance, confidence, reason, report_id=None,
                  report_conf=None, house_conf=None):
    """Update the current stance without entering/exiting."""
    pos = get_current_position(ticker)
    if not pos:
        return

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    old_stance = pos.get('current_stance', 'FADE')

    conn.execute("""
        UPDATE active_positions SET
            current_stance=?, stance_confidence=?,
            report_confidence=?, house_confidence=?,
            stance_updated_at=?, stance_report_id=?
        WHERE id=?
    """, (stance, confidence, report_conf, house_conf, now, report_id, pos['id']))
    conn.commit()
    conn.close()

    log_decision(
        ticker, 'STANCE_UPDATE', 'report' if report_id else 'autonomous',
        report_id, old_stance, stance, confidence, reason, None,
        report_confidence=report_conf, house_confidence=house_conf,
    )


def process_new_report(report, llm_trader=None):
    """
    Process a new report and make trading decisions.
    This is the PRIMARY decision input - daily report from Company Watch.

    Now tracks DUAL CONFIDENCE:
    - report_confidence: what the Company Watch report says
    - house_confidence: what our AI trading bot thinks after its own DD

    The house can agree (amplify to 83%), disagree (knock down to 40%),
    or even flip direction (e.g. report says HOLD 62% but house says SELL -40%).
    """
    ticker = WATCHED_TICKER
    report_stance = report.get('report_stance', 'HOLD')
    report_confidence = report.get('report_confidence', 50) or 50
    report_id = report.get('id')

    logger.info(
        "Processing report: stance=%s confidence=%s%%",
        report_stance, report_confidence
    )

    # Get current price
    price_data = fetch_price_av(ticker)
    if not price_data:
        logger.error("Cannot process report - no price data")
        return

    price = price_data['price']
    pos = get_or_create_position(ticker)
    current_state = pos.get('state', 'FLAT')

    # === DUAL CONFIDENCE ===
    # Start with report values, let LLM modify
    final_stance = report_stance
    final_confidence = report_confidence
    house_confidence = report_confidence  # Default: house agrees with report
    final_reason = report.get('report_rationale', 'Report recommendation')
    override = False

    if llm_trader:
        time.sleep(AV_RATE_LIMIT)
        llm_result = llm_trader.assess_report(report, price_data, pos)
        if llm_result:
            llm_stance = llm_result.get('decision', '').upper()
            llm_conf_level = llm_result.get('confidence', 'MEDIUM')
            llm_reason = llm_result.get('reason', '')
            llm_house_pct = llm_result.get('house_confidence_pct')

            # The house sets its OWN confidence number
            if llm_house_pct is not None:
                house_confidence = float(llm_house_pct)
            elif llm_conf_level == 'HIGH':
                house_confidence = max(report_confidence + 15, 80)
            elif llm_conf_level == 'LOW':
                house_confidence = min(report_confidence - 20, 35)
            else:
                house_confidence = report_confidence  # MEDIUM = agrees

            # The effective confidence is the house confidence
            final_confidence = house_confidence

            # LLM can override stance if it disagrees
            if llm_stance and llm_stance != report_stance:
                if llm_conf_level == 'HIGH' or abs(house_confidence - report_confidence) >= 20:
                    override = True
                    final_stance = llm_stance
                    final_reason = "House Override: " + llm_reason
                    logger.info(
                        "HOUSE OVERRIDE: report=%s(%s%%) -> house=%s(%s%%): %s",
                        report_stance, report_confidence,
                        llm_stance, house_confidence, llm_reason
                    )

    logger.info(
        "DUAL CONFIDENCE: Report=%s%% | House=%s%% | Final stance=%s",
        report_confidence, house_confidence, final_stance
    )

    # Decision matrix based on current state and new stance
    if current_state == 'FLAT':
        if final_stance == 'BUY' and final_confidence >= CONFIDENCE_ACT:
            enter_position(ticker, 'LONG', price, final_reason, report_id,
                           final_confidence, report_confidence, house_confidence)
        elif final_stance == 'SELL' and final_confidence >= CONFIDENCE_ACT:
            enter_position(ticker, 'SHORT', price, final_reason, report_id,
                           final_confidence, report_confidence, house_confidence)
        else:
            update_stance(ticker, final_stance, final_confidence, final_reason,
                          report_id, report_confidence, house_confidence)

    elif current_state == 'HELD':
        direction = pos.get('direction', 'LONG')

        if final_stance == 'SELL' and direction == 'LONG':
            exit_position(ticker, price, final_reason, report_id)
        elif final_stance == 'BUY' and direction == 'SHORT':
            exit_position(ticker, price, final_reason, report_id)
        elif final_stance == 'FADE':
            exit_position(ticker, price, "FADE: " + final_reason, report_id)
        else:
            update_stance(ticker, final_stance, final_confidence, final_reason,
                          report_id, report_confidence, house_confidence)

    # Log override
    if override:
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE active_positions SET
                was_overridden=1, override_reason=?, override_time=?
            WHERE id=? AND closed_at IS NULL
        """, (final_reason, now, pos['id']))
        conn.commit()
        conn.close()


def premarket_dd(llm_trader=None):
    """
    Pre-market due diligence - runs BEFORE NYSE opens.
    This is almost the first job outside of trading hours.

    Checks:
    1. Hong Kong tape (BABA is dual-listed on HKEX as 9988.HK)
    2. Overnight news and press
    3. Whether we need to duck-and-cover at open
    4. Any last-minute signals that change the thesis

    If it spots a storm coming, it can flag for duck-and-cover at market open.
    """
    ticker = WATCHED_TICKER
    pos = get_current_position(ticker)
    report = get_latest_report(ticker)

    logger.info("=== PRE-MARKET DD: %s ===", ticker)

    if not llm_trader:
        logger.info("No LLM trader available for pre-market DD")
        return

    # Get current price (may be delayed if market closed)
    price_data = fetch_price_av(ticker)

    # Ask LLM to do pre-market assessment
    llm_result = llm_trader.premarket_check(pos, price_data, report)
    if not llm_result:
        logger.info("Pre-market DD: LLM returned no result")
        return

    action = llm_result.get('action', 'HOLD').upper()
    urgency = llm_result.get('urgency', 'LOW').upper()
    reason = llm_result.get('reason', '')
    duck_recommend = llm_result.get('duck_and_cover', False)

    logger.info(
        "Pre-market DD result: action=%s urgency=%s duck=%s reason=%s",
        action, urgency, duck_recommend, reason
    )

    # If LLM recommends duck-and-cover, flag the position
    if duck_recommend and pos and pos.get('state') == 'HELD' and DUCK_COVER_ENABLED:
        conn = get_db()
        conn.execute("""
            UPDATE active_positions SET
                is_ducking=1, duck_reason=?
            WHERE id=? AND closed_at IS NULL
        """, (reason, pos['id']))
        conn.commit()
        conn.close()
        logger.info("DUCK-AND-COVER FLAGGED: Will sell at open, rebuy later. Reason: %s", reason)

        log_decision(
            ticker, 'PREMARKET_DUCK', 'premarket', None,
            pos.get('current_stance'), 'DUCK',
            0, "Pre-market: flagged for duck-and-cover. " + reason,
            price_data.get('price') if price_data else None,
            dd_type='premarket',
        )

    # If LLM says EXIT urgently, exit now (even pre-market, mark for open)
    if action == 'EXIT' and urgency == 'HIGH' and pos and pos.get('state') == 'HELD':
        if price_data:
            exit_position(ticker, price_data['price'],
                          "PRE-MARKET URGENT EXIT: " + reason)
        log_decision(
            ticker, 'PREMARKET_EXIT', 'premarket', None,
            pos.get('current_stance'), 'FADE',
            0, "Pre-market urgent exit: " + reason,
            price_data.get('price') if price_data else None,
            dd_type='premarket',
        )


def duck_and_cover_sell(llm_trader=None):
    """
    Duck-and-cover SELL phase.
    Called at market open (9:30 ET). If position is flagged for ducking,
    sell immediately to avoid the expected storm.
    """
    if not DUCK_COVER_ENABLED:
        return

    ticker = WATCHED_TICKER
    pos = get_current_position(ticker)
    if not pos or pos.get('state') != 'HELD':
        return
    if not pos.get('is_ducking'):
        return

    price_data = fetch_price_av(ticker)
    if not price_data:
        return

    price = price_data['price']
    reason = pos.get('duck_reason', 'Storm incoming - ducking')

    # Record duck exit details
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        UPDATE active_positions SET
            duck_exit_price=?, duck_exit_time=?
        WHERE id=?
    """, (price, now, pos['id']))
    conn.commit()
    conn.close()

    # Exit the position
    exit_position(ticker, price, "DUCK-AND-COVER SELL: " + reason)
    logger.info("DUCK SELL at $%.2f: %s", price, reason)


def duck_and_cover_rebuy(llm_trader=None):
    """
    Duck-and-cover REBUY phase.
    Called ~60 min after open (10:30 ET). Re-enter if the report thesis
    is still valid and conditions have calmed.
    """
    if not DUCK_COVER_ENABLED:
        return

    ticker = WATCHED_TICKER
    report = get_latest_report(ticker)
    if not report:
        return

    report_conf = report.get('report_confidence', 0) or 0
    report_stance = report.get('report_stance', 'HOLD')

    # Only re-buy if report confidence is high enough
    if report_conf < DUCK_MIN_CONFIDENCE:
        logger.info("Duck rebuy: report confidence %s%% below threshold %s%%, staying out",
                     report_conf, DUCK_MIN_CONFIDENCE)
        return

    # Check current conditions
    price_data = fetch_price_av(ticker)
    if not price_data:
        return

    price = price_data['price']

    # Ask LLM if it's safe to re-enter
    house_confidence = report_conf
    if llm_trader:
        time.sleep(AV_RATE_LIMIT)
        llm_result = llm_trader.assess_rebuy(report, price_data)
        if llm_result:
            action = llm_result.get('action', 'REBUY').upper()
            house_confidence = llm_result.get('house_confidence_pct', report_conf)

            if action == 'STAY_OUT':
                logger.info("Duck rebuy: LLM says stay out - %s",
                             llm_result.get('reason', ''))
                return

    # Re-enter based on report direction
    if report_stance in ('BUY', 'HOLD') and house_confidence >= CONFIDENCE_ACT:
        enter_position(ticker, 'LONG', price,
                       "DUCK REBUY: Storm passed, re-entering long",
                       confidence=house_confidence,
                       report_conf=report_conf,
                       house_conf=house_confidence)
    elif report_stance == 'SELL' and house_confidence >= CONFIDENCE_ACT:
        enter_position(ticker, 'SHORT', price,
                       "DUCK REBUY: Storm passed, re-entering short",
                       confidence=house_confidence,
                       report_conf=report_conf,
                       house_conf=house_confidence)
    else:
        logger.info("Duck rebuy: conditions not met for re-entry (stance=%s, conf=%s%%)",
                     report_stance, house_confidence)


def autonomous_dd(llm_trader=None):
    """
    Autonomous due diligence check - runs between reports.
    This is the "human in the loop" AI that can:
    1. Check if the thesis still holds
    2. Monitor for market events that override the report
    3. Apply profit-taking or stop-loss logic
    4. Detect directional mismatches
    """
    ticker = WATCHED_TICKER
    pos = get_current_position(ticker)
    if not pos or pos.get('state') == 'FLAT':
        return  # Nothing to check when flat

    # Get current price
    price_data = fetch_price_av(ticker)
    if not price_data:
        return

    price = price_data['price']
    direction = pos.get('direction', 'LONG')
    entry_price = pos.get('entry_price', price)
    peak_price = pos.get('peak_price', price)
    current_pnl = calculate_pnl(entry_price, price, direction)

    # Get latest report for context
    report = get_latest_report(ticker)

    # === CHECK 1: Hard stop-loss ===
    if current_pnl <= LOSS_STOP_HARD_PCT:
        exit_position(ticker, price,
                      "HARD STOP: P&L at {:.1f}% exceeds hard stop of {:.1f}%".format(
                          current_pnl, LOSS_STOP_HARD_PCT))
        return

    # === CHECK 2: Profit-taking from peak drawdown ===
    if peak_price and entry_price:
        peak_pnl = calculate_pnl(entry_price, peak_price, direction)
        if peak_pnl >= PROFIT_TAKE_PCT:
            drawdown = peak_pnl - current_pnl
            if drawdown >= DRAWDOWN_FROM_PEAK_PCT:
                exit_position(ticker, price,
                              "PROFIT PROTECT: Peak P&L was {:.1f}%, now {:.1f}% (drawdown {:.1f}%)".format(
                                  peak_pnl, current_pnl, drawdown))
                return

    # === CHECK 3: Strong profit-taking ===
    if current_pnl >= PROFIT_TAKE_STRONG_PCT:
        report_conf = report.get('report_confidence', 50) if report else 50
        if report_conf < 75:
            exit_position(ticker, price,
                          "PROFIT TAKE: P&L at {:.1f}% with report confidence only {:.0f}%".format(
                              current_pnl, report_conf))
            return

    # === CHECK 4: Market crash override ===
    spy_data = fetch_spy_price()
    if spy_data:
        spy_change = spy_data.get('change_pct', 0)
        if spy_change <= OVERRIDE_MARKET_CRASH_PCT and direction == 'LONG':
            exit_position(ticker, price,
                          "MARKET CRASH OVERRIDE: S&P down {:.1f}%, protecting long position".format(
                              spy_change))
            log_decision(
                ticker, 'OVERRIDE', 'autonomous', None,
                pos.get('current_stance'), 'FADE', 0,
                "Market crash detected", price,
                spy_price=spy_data.get('price'),
                spy_change=spy_change,
                is_override=True, override_what='market_crash'
            )
            return

    # === CHECK 5: Large price move since report (horse bolted?) ===
    if report and report.get('report_confidence'):
        conn = get_db()
        report_snap = conn.execute(
            """SELECT price FROM price_snapshots
               WHERE ticker=? AND timestamp >= ? ORDER BY timestamp ASC LIMIT 1""",
            (ticker, report.get('published_date', ''))
        ).fetchone()
        conn.close()

        if report_snap:
            report_price = report_snap['price']
            move_since_report = ((price - report_price) / report_price) * 100

            if abs(move_since_report) >= OVERRIDE_PRICE_MOVE_PCT:
                if (direction == 'LONG' and move_since_report < -OVERRIDE_PRICE_MOVE_PCT):
                    exit_position(ticker, price,
                                  "HORSE BOLTED: Price down {:.1f}% since report, thesis may be invalidated".format(
                                      move_since_report))
                    return
                elif (direction == 'SHORT' and move_since_report > OVERRIDE_PRICE_MOVE_PCT):
                    exit_position(ticker, price,
                                  "HORSE BOLTED: Price up {:.1f}% since report, short thesis may be wrong".format(
                                      move_since_report))
                    return

    # === CHECK 6: Soft stop-loss with LLM assessment ===
    if current_pnl <= LOSS_STOP_PCT and llm_trader:
        llm_result = llm_trader.assess_loss(pos, price_data, report)
        if llm_result and llm_result.get('action') == 'EXIT':
            exit_position(ticker, price,
                          "AI STOP: " + llm_result.get('reason', 'Thesis no longer valid'))
            return

    # === CHECK 7: LLM autonomous assessment ===
    if llm_trader:
        llm_result = llm_trader.autonomous_check(pos, price_data, report)
        if llm_result:
            action = llm_result.get('action', 'HOLD').upper()
            if action == 'EXIT':
                exit_position(ticker, price,
                              "AI DD EXIT: " + llm_result.get('reason', 'Conditions changed'))
            elif action == 'REVERSE':
                exit_position(ticker, price,
                              "AI DD REVERSE: " + llm_result.get('reason', 'Direction reversed'))
                new_dir = 'SHORT' if direction == 'LONG' else 'LONG'
                time.sleep(1)
                enter_position(ticker, new_dir, price,
                               "AI REVERSE: " + llm_result.get('reason', ''))

    logger.info(
        "Autonomous DD complete for %s: P&L=%.2f%%, holding %s",
        ticker, current_pnl, direction
    )


def is_market_open():
    """Check if US market is currently open."""
    now = datetime.now(timezone.utc)
    if now.weekday() not in MARKET_DAYS:
        return False
    hour = now.hour + now.minute / 60.0
    return MARKET_OPEN_UTC <= hour < MARKET_CLOSE_UTC


def is_premarket_window():
    """Check if we're in the pre-market DD window (before NYSE open)."""
    now = datetime.now(timezone.utc)
    if now.weekday() not in MARKET_DAYS:
        return False
    hour = now.hour + now.minute / 60.0
    window_start = MARKET_OPEN_UTC - PREMARKET_WINDOW_HOURS_BEFORE_OPEN
    return window_start <= hour < MARKET_OPEN_UTC


def minutes_since_market_open():
    """How many minutes since NYSE opened. Negative if before open."""
    now = datetime.now(timezone.utc)
    open_minutes = int(MARKET_OPEN_UTC * 60)
    current_minutes = now.hour * 60 + now.minute
    return current_minutes - open_minutes
