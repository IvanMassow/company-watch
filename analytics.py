"""
Company Watch - Analytics Engine
Computes performance metrics comparing two parallel lines:
  1. ACTIVE line: AI-managed trading (buy/sell/hold based on reports + DD)
  2. PASSIVE line: Buy-and-hold benchmark

The core question: Does our intelligence system beat just sitting there?
"""
import logging
from datetime import datetime, timezone

from config import WATCHED_TICKER
from db import (
    get_db, get_current_position, get_passive_position,
    get_recent_decisions, get_daily_summaries, get_price_history,
)

logger = logging.getLogger("companywatch.analytics")


def generate_analytics():
    """
    Generate comprehensive analytics for report rendering.
    Returns a dict with all data needed for the HTML report.
    """
    ticker = WATCHED_TICKER
    conn = get_db()

    # Current positions
    active_pos = get_current_position(ticker)
    passive_pos = get_passive_position(ticker)

    # Latest price
    latest_snap = conn.execute(
        "SELECT * FROM price_snapshots WHERE ticker=? ORDER BY timestamp DESC LIMIT 1",
        (ticker,)
    ).fetchone()
    latest_price = dict(latest_snap) if latest_snap else {}

    # Recent decisions
    decisions = get_recent_decisions(ticker, limit=30)

    # Daily summaries for charting
    daily = get_daily_summaries(ticker, limit=90)

    # Price history (last 96 hours for timeline)
    price_history = get_price_history(ticker, hours=96)

    # Reports
    reports = conn.execute(
        "SELECT * FROM reports WHERE ticker=? ORDER BY published_date DESC LIMIT 30",
        (ticker,)
    ).fetchall()
    reports = [dict(r) for r in reports]

    # All closed positions (for trade history)
    closed = conn.execute(
        """SELECT * FROM active_positions WHERE ticker=? AND closed_at IS NOT NULL
           ORDER BY closed_at DESC LIMIT 50""",
        (ticker,)
    ).fetchall()
    closed_positions = [dict(r) for r in closed]

    conn.close()

    # Compute summary metrics
    summary = _compute_summary(active_pos, passive_pos, latest_price, closed_positions, daily)

    # Compute trade statistics
    trade_stats = _compute_trade_stats(closed_positions)

    # Compute decision analysis
    decision_analysis = _compute_decision_analysis(decisions)

    # Compute override stats
    override_stats = _compute_override_stats(decisions)

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'ticker': ticker,
        'active_position': dict(active_pos) if active_pos else None,
        'passive_position': dict(passive_pos) if passive_pos else None,
        'latest_price': latest_price,
        'summary': summary,
        'trade_stats': trade_stats,
        'decision_analysis': decision_analysis,
        'override_stats': override_stats,
        'decisions': decisions,
        'daily': daily,
        'price_history': price_history,
        'reports': reports,
        'closed_positions': closed_positions,
    }


def _compute_summary(active_pos, passive_pos, latest_price, closed_positions, daily):
    """Compute high-level summary metrics."""
    price = latest_price.get('price', 0) if latest_price else 0

    # Active line metrics
    active_unrealised = 0
    active_state = 'FLAT'
    active_direction = None
    active_entry = None

    if active_pos and active_pos.get('state') != 'FLAT':
        active_state = active_pos['state']
        active_direction = active_pos.get('direction')
        active_entry = active_pos.get('entry_price')
        if active_entry and price:
            from tracker import calculate_pnl
            active_unrealised = calculate_pnl(active_entry, price, active_direction or 'LONG')

    # Total realised P&L
    realised_pnl = sum(
        p.get('realised_pnl_pct', 0) or 0
        for p in closed_positions
    )

    # Passive line metrics
    passive_pnl = 0
    passive_entry = None
    if passive_pos and passive_pos.get('entry_price') and price:
        passive_entry = passive_pos['entry_price']
        passive_pnl = ((price - passive_entry) / passive_entry) * 100

    # Alpha = active total - passive
    active_total = realised_pnl + active_unrealised
    alpha = active_total - passive_pnl

    # Win rate from closed trades
    wins = sum(1 for p in closed_positions if (p.get('realised_pnl_pct') or 0) > 0)
    total_trades = len(closed_positions)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    # Days tracked
    days_tracked = len(daily)

    # Total reports received
    report_days = sum(1 for d in daily if d.get('report_received'))

    return {
        'current_price': price,
        'active_state': active_state,
        'active_direction': active_direction,
        'active_entry_price': active_entry,
        'active_unrealised_pnl': active_unrealised,
        'active_realised_pnl': realised_pnl,
        'active_total_pnl': active_total,
        'passive_entry_price': passive_entry,
        'passive_pnl': passive_pnl,
        'alpha': alpha,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'days_tracked': days_tracked,
        'report_days': report_days,
    }


def _compute_trade_stats(closed_positions):
    """Compute statistics on completed trades."""
    if not closed_positions:
        return {
            'total': 0, 'wins': 0, 'losses': 0,
            'avg_win': 0, 'avg_loss': 0,
            'best': 0, 'worst': 0,
            'avg_hold_hours': 0,
        }

    pnls = [(p.get('realised_pnl_pct') or 0) for p in closed_positions]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # Average hold time
    hold_hours = []
    for p in closed_positions:
        if p.get('entry_time') and p.get('exit_time'):
            try:
                entry = datetime.fromisoformat(p['entry_time'].replace('Z', '+00:00'))
                exit_t = datetime.fromisoformat(p['exit_time'].replace('Z', '+00:00'))
                hold_hours.append((exit_t - entry).total_seconds() / 3600)
            except Exception:
                pass

    return {
        'total': len(pnls),
        'wins': len(wins),
        'losses': len(losses),
        'avg_win': sum(wins) / len(wins) if wins else 0,
        'avg_loss': sum(losses) / len(losses) if losses else 0,
        'best': max(pnls) if pnls else 0,
        'worst': min(pnls) if pnls else 0,
        'avg_hold_hours': sum(hold_hours) / len(hold_hours) if hold_hours else 0,
    }


def _compute_decision_analysis(decisions):
    """Analyze decision patterns."""
    if not decisions:
        return {
            'total_decisions': 0,
            'entries': 0, 'exits': 0, 'stance_updates': 0,
            'overrides': 0, 'report_triggered': 0, 'autonomous': 0,
        }

    entries = sum(1 for d in decisions if d.get('decision_type') == 'ENTRY')
    exits = sum(1 for d in decisions if d.get('decision_type') == 'EXIT')
    stance_updates = sum(1 for d in decisions if d.get('decision_type') == 'STANCE_UPDATE')
    overrides = sum(1 for d in decisions if d.get('is_override'))
    report_triggered = sum(1 for d in decisions if d.get('trigger') == 'report')
    autonomous = sum(1 for d in decisions if d.get('trigger') == 'autonomous')

    return {
        'total_decisions': len(decisions),
        'entries': entries,
        'exits': exits,
        'stance_updates': stance_updates,
        'overrides': overrides,
        'report_triggered': report_triggered,
        'autonomous': autonomous,
    }


def _compute_override_stats(decisions):
    """Analyze how often and why the AI overrides reports."""
    overrides = [d for d in decisions if d.get('is_override')]
    if not overrides:
        return {
            'total': 0, 'reasons': {},
            'override_rate': 0,
        }

    reasons = {}
    for o in overrides:
        what = o.get('override_what', 'unknown')
        reasons[what] = reasons.get(what, 0) + 1

    report_decisions = sum(1 for d in decisions if d.get('trigger') == 'report')
    rate = (len(overrides) / report_decisions * 100) if report_decisions > 0 else 0

    return {
        'total': len(overrides),
        'reasons': reasons,
        'override_rate': rate,
    }


def generate_briefing():
    """Generate a text briefing for quick review."""
    data = generate_analytics()
    s = data['summary']

    lines = [
        "=== COMPANY WATCH: {} ===".format(data['ticker']),
        "Generated: {}".format(data['generated_at'][:16]),
        "",
        "CURRENT PRICE: ${:.2f}".format(s['current_price']),
        "",
        "--- ACTIVE LINE (AI-Managed) ---",
        "State: {} {}".format(s['active_state'], s['active_direction'] or ''),
        "Entry: {}".format('${:.2f}'.format(s['active_entry_price']) if s['active_entry_price'] else 'FLAT'),
        "Unrealised P&L: {:.2f}%".format(s['active_unrealised_pnl']),
        "Realised P&L: {:.2f}%".format(s['active_realised_pnl']),
        "Total P&L: {:.2f}%".format(s['active_total_pnl']),
        "",
        "--- PASSIVE LINE (Buy & Hold) ---",
        "Entry: {}".format('${:.2f}'.format(s['passive_entry_price']) if s['passive_entry_price'] else 'N/A'),
        "P&L: {:.2f}%".format(s['passive_pnl']),
        "",
        "--- ALPHA ---",
        "Active vs Passive: {:+.2f}%".format(s['alpha']),
        "Winner: {}".format('ACTIVE' if s['alpha'] > 0 else 'PASSIVE' if s['alpha'] < 0 else 'TIED'),
        "",
        "--- TRADING STATS ---",
        "Total Trades: {}".format(s['total_trades']),
        "Win Rate: {:.1f}%".format(s['win_rate']),
        "Days Tracked: {}".format(s['days_tracked']),
        "Reports Received: {}".format(s['report_days']),
    ]

    return '\n'.join(lines)
