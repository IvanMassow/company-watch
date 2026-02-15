"""
Company Watch - Price Tracker
Fetches prices via Alpha Vantage and maintains two parallel tracking lines:
  1. ACTIVE line: P&L based on AI trading decisions
  2. PASSIVE line: P&L based on buy-and-hold from first report
"""
import logging
import time
from datetime import datetime, timezone

import requests

from config import (
    ALPHA_VANTAGE_KEY, ALPHA_VANTAGE_BASE, AV_RATE_LIMIT,
    WATCHED_TICKER,
)
from db import get_db, get_current_position, get_passive_position

logger = logging.getLogger("companywatch.tracker")

# Cache to avoid duplicate API calls within same cycle
_price_cache = {}


def fetch_price_av(ticker):
    """
    Fetch current price from Alpha Vantage GLOBAL_QUOTE.
    Returns dict with price data or None on failure.
    """
    if not ALPHA_VANTAGE_KEY:
        logger.warning("No ALPHA_VANTAGE_KEY set")
        return None

    # Check cache (within same minute)
    cache_key = ticker
    if cache_key in _price_cache:
        cached_time, cached_data = _price_cache[cache_key]
        if (datetime.now(timezone.utc) - cached_time).total_seconds() < 60:
            return cached_data

    try:
        resp = requests.get(ALPHA_VANTAGE_BASE, params={
            'function': 'GLOBAL_QUOTE',
            'symbol': ticker,
            'apikey': ALPHA_VANTAGE_KEY,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if 'Note' in data or 'Information' in data:
            logger.warning("AV rate limit hit for %s", ticker)
            return None

        quote = data.get('Global Quote', {})
        if not quote or '05. price' not in quote:
            logger.warning("No quote data for %s", ticker)
            return None

        result = {
            'price': float(quote.get('05. price', 0)),
            'open': float(quote.get('02. open', 0)),
            'high': float(quote.get('03. high', 0)),
            'low': float(quote.get('04. low', 0)),
            'volume': float(quote.get('06. volume', 0)),
            'change_pct': float(quote.get('10. change percent', '0').rstrip('%')),
        }

        _price_cache[cache_key] = (datetime.now(timezone.utc), result)
        logger.info("Price for %s: $%.2f (%.2f%%)", ticker, result['price'], result['change_pct'])
        return result

    except Exception as e:
        logger.error("AV fetch failed for %s: %s", ticker, e)
        return None


def fetch_spy_price():
    """Fetch SPY price for market context."""
    time.sleep(AV_RATE_LIMIT)
    return fetch_price_av("SPY")


def calculate_pnl(entry_price, current_price, direction="LONG"):
    """Calculate P&L percentage."""
    if not entry_price or entry_price == 0:
        return 0.0
    if direction == "SHORT":
        return (entry_price - current_price) / entry_price * 100
    return (current_price - entry_price) / entry_price * 100


def track_prices(ticker=None):
    """
    Fetch current price and record snapshots for both active and passive lines.
    """
    ticker = ticker or WATCHED_TICKER
    now = datetime.now(timezone.utc)

    # Fetch price
    price_data = fetch_price_av(ticker)
    if not price_data:
        logger.warning("Could not fetch price for %s", ticker)
        return False

    price = price_data['price']
    conn = get_db()

    # Check for recent snapshot to avoid duplicates (within 50 min)
    recent = conn.execute(
        """SELECT id FROM price_snapshots
           WHERE ticker=? AND timestamp > datetime('now', '-50 minutes')""",
        (ticker,)
    ).fetchone()

    if recent:
        conn.close()
        logger.debug("Skipping snapshot - recent one exists")
        return True

    # Calculate P&L for both lines
    active_pos = get_current_position(ticker)
    passive_pos = get_passive_position(ticker)

    active_pnl = None
    active_state = 'FLAT'
    if active_pos and active_pos.get('entry_price') and active_pos.get('state') != 'FLAT':
        direction = active_pos.get('direction', 'LONG')
        active_pnl = calculate_pnl(active_pos['entry_price'], price, direction)
        active_state = active_pos.get('state', 'FLAT')

        # Update peak/trough tracking
        peak = active_pos.get('peak_price') or price
        trough = active_pos.get('trough_price') or price
        if price > peak:
            conn.execute("UPDATE active_positions SET peak_price=? WHERE id=?",
                         (price, active_pos['id']))
        if price < trough:
            conn.execute("UPDATE active_positions SET trough_price=? WHERE id=?",
                         (price, active_pos['id']))

    passive_pnl = None
    if passive_pos and passive_pos.get('entry_price'):
        passive_pnl = calculate_pnl(passive_pos['entry_price'], price, 'LONG')

    # Insert snapshot
    conn.execute("""
        INSERT OR IGNORE INTO price_snapshots
        (ticker, timestamp, price, open_price, high, low, volume, change_pct,
         active_pnl_pct, passive_pnl_pct, active_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker,
        now.isoformat(),
        price,
        price_data.get('open'),
        price_data.get('high'),
        price_data.get('low'),
        price_data.get('volume'),
        price_data.get('change_pct'),
        active_pnl,
        passive_pnl,
        active_state,
    ))
    conn.commit()
    conn.close()

    logger.info(
        "Snapshot: %s $%.2f | Active P&L: %s | Passive P&L: %s",
        ticker, price,
        "{:.2f}%".format(active_pnl) if active_pnl is not None else "FLAT",
        "{:.2f}%".format(passive_pnl) if passive_pnl is not None else "N/A",
    )
    return True


def ensure_passive_position(ticker=None):
    """
    Ensure the passive (buy-and-hold) position exists.
    Created on first price fetch if not already present.
    """
    ticker = ticker or WATCHED_TICKER
    passive = get_passive_position(ticker)
    if passive:
        return passive

    # Create passive position at current price
    price_data = fetch_price_av(ticker)
    if not price_data:
        return None

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO passive_position (ticker, entry_price, entry_time) VALUES (?, ?, ?)",
        (ticker, price_data['price'], now)
    )
    conn.commit()
    conn.close()

    logger.info("Passive position opened: %s @ $%.2f", ticker, price_data['price'])
    return get_passive_position(ticker)


def update_daily_summary(ticker=None):
    """
    Update the daily summary table with today's data.
    Called after each price tracking cycle.
    """
    ticker = ticker or WATCHED_TICKER
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    conn = get_db()

    # Get today's price range from snapshots
    snapshots = conn.execute(
        """SELECT price, active_pnl_pct, passive_pnl_pct, active_state
           FROM price_snapshots WHERE ticker=? AND date(timestamp)=?
           ORDER BY timestamp ASC""",
        (ticker, today)
    ).fetchall()

    if not snapshots:
        conn.close()
        return

    prices = [s['price'] for s in snapshots if s['price']]
    if not prices:
        conn.close()
        return

    open_price = prices[0]
    close_price = prices[-1]
    high_price = max(prices)
    low_price = min(prices)

    # Latest P&L values
    last = snapshots[-1]
    active_pnl = last['active_pnl_pct']
    passive_pnl = last['passive_pnl_pct']
    active_state = last['active_state']

    # Check if report was received today
    report = conn.execute(
        "SELECT report_stance, report_confidence FROM reports WHERE ticker=? AND date(published_date)=?",
        (ticker, today)
    ).fetchone()

    report_received = 1 if report else 0
    report_stance = report['report_stance'] if report else None
    report_conf = report['report_confidence'] if report else None

    # Calculate alpha
    alpha = None
    if active_pnl is not None and passive_pnl is not None:
        alpha = active_pnl - passive_pnl

    # Calculate cumulative P&L (sum of daily changes)
    active_pos = get_current_position(ticker)
    passive_pos = get_passive_position(ticker)

    active_cum = active_pnl  # For now, use current unrealised
    passive_cum = passive_pnl

    position_held = 1 if active_state not in ('FLAT', None) else 0

    conn.execute("""
        INSERT OR REPLACE INTO daily_summary
        (ticker, date, open_price, close_price, high_price, low_price,
         active_stance, active_pnl_pct, active_cumulative_pnl, active_position_held,
         passive_pnl_pct, passive_cumulative_pnl,
         report_received, report_stance, report_confidence, alpha_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker, today, open_price, close_price, high_price, low_price,
        active_state, active_pnl, active_cum, position_held,
        passive_pnl, passive_cum,
        report_received, report_stance, report_conf, alpha,
    ))
    conn.commit()
    conn.close()
