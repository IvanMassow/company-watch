"""
Company Watch - HTML Report Generator
Noah Pink design system adapted for Company Watch.
Shows two competing lines: Active (AI-managed) vs Passive (buy-and-hold).

Three-act structure:
1. The Arena - Current state, both lines, latest stance
2. The Ledger - Trade history, decision log, overrides
3. The Scoreboard - Performance comparison, alpha tracking
"""
import os
import logging
from datetime import datetime, timezone

from analytics import generate_analytics
from config import REPORTS_DIR, WATCHED_TICKER

logger = logging.getLogger("companywatch.report")


def _stance_color(stance):
    return {
        'BUY': '#16a34a', 'SELL': '#cc0000', 'HOLD': '#d97706',
        'FADE': '#9ea2b0', 'FLAT': '#9ea2b0',
    }.get(stance, '#9ea2b0')


def _stance_bg(stance):
    return {
        'BUY': '#dcfce7', 'SELL': '#fef2f2', 'HOLD': '#fef3c7',
        'FADE': '#f1f5f9', 'FLAT': '#f1f5f9',
    }.get(stance, '#f1f5f9')


def _pnl_color(pnl):
    if pnl > 0:
        return '#16a34a'
    elif pnl < 0:
        return '#cc0000'
    return '#9ea2b0'


def _pnl_arrow(pnl):
    if pnl > 0:
        return '&#9650;'  # up triangle
    elif pnl < 0:
        return '&#9660;'  # down triangle
    return '&#9644;'  # dash


def generate_html_report():
    """Generate the full HTML report and save to reports directory."""
    data = generate_analytics()
    s = data['summary']

    now = datetime.now(timezone.utc)
    timestamp = now.strftime('%Y-%m-%d %H:%M UTC')

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Company Watch: {ticker} | {timestamp}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=Lato:wght@300;400;700&family=Montserrat:wght@500;700&display=swap');

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: 'Lato', sans-serif;
    background: #FFF1E5;
    color: #33302e;
    line-height: 1.6;
    padding: 0;
}}

.container {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 20px;
}}

/* Header */
.header {{
    background: linear-gradient(135deg, #0d7680, #1a9ba5);
    color: white;
    padding: 30px 40px;
    border-radius: 12px;
    margin-bottom: 24px;
}}
.header h1 {{
    font-family: 'Playfair Display', serif;
    font-size: 2.2em;
    margin-bottom: 4px;
}}
.header .subtitle {{
    font-family: 'Montserrat', sans-serif;
    font-size: 0.9em;
    opacity: 0.9;
}}
.header .price-hero {{
    font-family: 'Montserrat', sans-serif;
    font-size: 2.8em;
    font-weight: 700;
    margin: 16px 0 8px;
}}
.header .price-change {{
    font-size: 1.1em;
    opacity: 0.9;
}}

/* Section headers */
.act-header {{
    font-family: 'Playfair Display', serif;
    font-size: 1.4em;
    color: #0d7680;
    border-bottom: 2px solid #0d7680;
    padding-bottom: 6px;
    margin: 32px 0 16px;
}}

/* Cards */
.card {{
    background: white;
    border-radius: 10px;
    padding: 20px 24px;
    margin-bottom: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}}
.card-title {{
    font-family: 'Montserrat', sans-serif;
    font-weight: 700;
    font-size: 0.85em;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #73788a;
    margin-bottom: 12px;
}}

/* Two-line comparison */
.lines-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
}}
.line-card {{
    background: white;
    border-radius: 10px;
    padding: 20px 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    border-top: 4px solid;
}}
.line-active {{ border-top-color: #0d7680; }}
.line-passive {{ border-top-color: #d97706; }}
.line-label {{
    font-family: 'Montserrat', sans-serif;
    font-weight: 700;
    font-size: 0.8em;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 8px;
}}
.line-active .line-label {{ color: #0d7680; }}
.line-passive .line-label {{ color: #d97706; }}
.line-pnl {{
    font-family: 'Montserrat', sans-serif;
    font-size: 2em;
    font-weight: 700;
}}
.line-detail {{
    font-size: 0.85em;
    color: #73788a;
    margin-top: 4px;
}}

/* Alpha badge */
.alpha-card {{
    background: white;
    border-radius: 10px;
    padding: 16px 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    text-align: center;
    margin-bottom: 16px;
}}
.alpha-value {{
    font-family: 'Montserrat', sans-serif;
    font-size: 2.4em;
    font-weight: 700;
}}
.alpha-label {{
    font-size: 0.85em;
    color: #73788a;
    margin-top: 4px;
}}

/* Stance badge */
.stance-badge {{
    display: inline-block;
    padding: 4px 14px;
    border-radius: 20px;
    font-family: 'Montserrat', sans-serif;
    font-weight: 700;
    font-size: 0.85em;
    letter-spacing: 0.03em;
}}

/* Tables */
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85em;
}}
th {{
    font-family: 'Montserrat', sans-serif;
    font-weight: 700;
    font-size: 0.75em;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #73788a;
    text-align: left;
    padding: 8px 10px;
    border-bottom: 2px solid #e8e4e0;
}}
td {{
    padding: 8px 10px;
    border-bottom: 1px solid #f0ece8;
    vertical-align: top;
}}
tr:hover {{ background: #fdf8f4; }}

/* Report card */
.report-card {{
    background: white;
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 10px;
    border-left: 4px solid;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}}

/* Chart area (placeholder for inline sparkline) */
.chart-area {{
    background: white;
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    min-height: 200px;
}}

/* Footer */
.footer {{
    text-align: center;
    padding: 24px;
    color: #9ea2b0;
    font-size: 0.8em;
    margin-top: 32px;
}}

/* Responsive */
@media (max-width: 768px) {{
    .lines-grid {{ grid-template-columns: 1fr; }}
    .header h1 {{ font-size: 1.6em; }}
    .header .price-hero {{ font-size: 2em; }}
}}
</style>
</head>
<body>
<div class="container">
""".format(ticker=WATCHED_TICKER, timestamp=timestamp)

    # === HEADER ===
    change_pct = data['latest_price'].get('change_pct', 0) if data['latest_price'] else 0
    change_arrow = _pnl_arrow(change_pct)
    change_color = _pnl_color(change_pct)

    html += """
<div class="header">
    <div class="subtitle">COMPANY WATCH</div>
    <h1>{ticker}</h1>
    <div class="price-hero">${price:.2f}</div>
    <div class="price-change" style="color:{cc}">{arrow} {change:+.2f}% today</div>
    <div class="subtitle" style="margin-top:12px">{timestamp}</div>
</div>
""".format(
        ticker=WATCHED_TICKER,
        price=s['current_price'],
        cc=change_color,
        arrow=change_arrow,
        change=change_pct,
        timestamp=timestamp,
    )

    # === ACT 1: THE ARENA ===
    html += '<h2 class="act-header">Act I: The Arena</h2>'

    # Current stance badge
    stance = s.get('active_state', 'FLAT')
    stance_label = stance
    if s.get('active_direction'):
        stance_label = s['active_direction'] + ' (' + stance + ')'

    active_pos = data.get('active_position')
    stance_from_pos = 'FADE'
    stance_conf = 0
    if active_pos:
        stance_from_pos = active_pos.get('current_stance', 'FADE')
        stance_conf = active_pos.get('stance_confidence', 0) or 0

    html += """
<div class="card">
    <div class="card-title">Current Stance</div>
    <span class="stance-badge" style="background:{bg};color:{fg}">{stance}</span>
    <span style="margin-left:12px;font-size:0.9em;color:#73788a">
        Confidence: {conf:.0f}%
    </span>
</div>
""".format(
        stance=stance_from_pos,
        bg=_stance_bg(stance_from_pos),
        fg=_stance_color(stance_from_pos),
        conf=stance_conf,
    )

    # Two-line comparison cards
    active_pnl = s['active_unrealised_pnl']
    passive_pnl = s['passive_pnl']
    alpha = s['alpha']

    html += """
<div class="lines-grid">
    <div class="line-card line-active">
        <div class="line-label">Active Line (AI-Managed)</div>
        <div class="line-pnl" style="color:{ac}">{aa} {apnl:+.2f}%</div>
        <div class="line-detail">
            State: {state} {direction}<br>
            Entry: {entry}<br>
            Realised: {realised:+.2f}% | Total: {total:+.2f}%
        </div>
    </div>
    <div class="line-card line-passive">
        <div class="line-label">Passive Line (Buy &amp; Hold)</div>
        <div class="line-pnl" style="color:{pc}">{pa} {ppnl:+.2f}%</div>
        <div class="line-detail">
            Entry: {pentry}<br>
            Strategy: Hold forever, take the hits and the good times
        </div>
    </div>
</div>
""".format(
        ac=_pnl_color(active_pnl),
        aa=_pnl_arrow(active_pnl),
        apnl=active_pnl,
        state=s['active_state'],
        direction=s['active_direction'] or '',
        entry='${:.2f}'.format(s['active_entry_price']) if s['active_entry_price'] else 'FLAT',
        realised=s['active_realised_pnl'],
        total=s['active_total_pnl'],
        pc=_pnl_color(passive_pnl),
        pa=_pnl_arrow(passive_pnl),
        ppnl=passive_pnl,
        pentry='${:.2f}'.format(s['passive_entry_price']) if s['passive_entry_price'] else 'N/A',
    )

    # Alpha card
    alpha_winner = 'Active wins' if alpha > 0 else 'Passive wins' if alpha < 0 else 'Tied'
    html += """
<div class="alpha-card">
    <div class="alpha-value" style="color:{ac}">{alpha:+.2f}%</div>
    <div class="alpha-label">Alpha (Active - Passive) &middot; {winner}</div>
</div>
""".format(ac=_pnl_color(alpha), alpha=alpha, winner=alpha_winner)

    # Price chart (inline SVG sparkline)
    html += _build_price_chart(data['price_history'], data.get('active_position'), data.get('passive_position'))

    # Latest report card
    if data['reports']:
        latest = data['reports'][0]
        r_stance = latest.get('report_stance', 'N/A')
        r_conf = latest.get('report_confidence', 0) or 0
        r_rationale = latest.get('report_rationale', '') or ''
        r_date = (latest.get('published_date', '') or '')[:16]

        html += """
<div class="report-card" style="border-left-color:{sc}">
    <div class="card-title">Latest Report ({date})</div>
    <span class="stance-badge" style="background:{bg};color:{fg}">{stance}</span>
    <span style="margin-left:8px;font-size:0.85em">Confidence: {conf:.0f}%</span>
    <p style="margin-top:8px;font-size:0.9em;color:#555">{rationale}</p>
</div>
""".format(
            sc=_stance_color(r_stance),
            date=r_date,
            bg=_stance_bg(r_stance),
            fg=_stance_color(r_stance),
            stance=r_stance,
            conf=r_conf,
            rationale=r_rationale[:300],
        )

    # === ACT 2: THE LEDGER ===
    html += '<h2 class="act-header">Act II: The Ledger</h2>'

    # Decision log table
    html += '<div class="card"><div class="card-title">Recent Decisions</div>'
    html += '<table><tr><th>Time</th><th>Type</th><th>From</th><th>To</th><th>Conf</th><th>Trigger</th><th>Reason</th></tr>'

    for d in data['decisions'][:15]:
        ts = (d.get('timestamp', '') or '')[:16]
        override_marker = ' &#9889;' if d.get('is_override') else ''
        html += """<tr>
            <td>{ts}</td>
            <td>{dtype}{override}</td>
            <td><span style="color:{ofc}">{old}</span></td>
            <td><span style="color:{nfc}">{new}</span></td>
            <td>{conf}</td>
            <td>{trigger}</td>
            <td style="font-size:0.8em">{reason}</td>
        </tr>""".format(
            ts=ts,
            dtype=d.get('decision_type', ''),
            override=override_marker,
            ofc=_stance_color(d.get('old_stance', '')),
            old=d.get('old_stance', ''),
            nfc=_stance_color(d.get('new_stance', '')),
            new=d.get('new_stance', ''),
            conf='{:.0f}%'.format(d['confidence']) if d.get('confidence') else '',
            trigger=d.get('trigger', ''),
            reason=(d.get('reason', '') or '')[:120],
        )

    html += '</table></div>'

    # Closed trades table
    if data['closed_positions']:
        html += '<div class="card"><div class="card-title">Trade History</div>'
        html += '<table><tr><th>Direction</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th><th>Duration</th></tr>'

        for p in data['closed_positions'][:20]:
            pnl = p.get('realised_pnl_pct', 0) or 0
            entry_time = (p.get('entry_time', '') or '')[:10]
            exit_time = (p.get('exit_time', '') or '')[:10]

            # Duration
            duration = ''
            if p.get('entry_time') and p.get('exit_time'):
                try:
                    e = datetime.fromisoformat(p['entry_time'].replace('Z', '+00:00'))
                    x = datetime.fromisoformat(p['exit_time'].replace('Z', '+00:00'))
                    hours = (x - e).total_seconds() / 3600
                    if hours >= 24:
                        duration = '{:.0f}d'.format(hours / 24)
                    else:
                        duration = '{:.0f}h'.format(hours)
                except Exception:
                    pass

            html += """<tr>
                <td>{dir}</td>
                <td>${entry:.2f}<br><small>{et}</small></td>
                <td>${exit:.2f}<br><small>{xt}</small></td>
                <td style="color:{pc};font-weight:700">{pnl:+.2f}%</td>
                <td style="font-size:0.8em">{reason}</td>
                <td>{dur}</td>
            </tr>""".format(
                dir=p.get('direction', ''),
                entry=p.get('entry_price', 0) or 0,
                et=entry_time,
                exit=p.get('exit_price', 0) or 0,
                xt=exit_time,
                pc=_pnl_color(pnl),
                pnl=pnl,
                reason=(p.get('exit_reason', '') or '')[:100],
                dur=duration,
            )

        html += '</table></div>'

    # === ACT 3: THE SCOREBOARD ===
    html += '<h2 class="act-header">Act III: The Scoreboard</h2>'

    ts = data.get('trade_stats', {})
    da = data.get('decision_analysis', {})
    ov = data.get('override_stats', {})

    # Stats grid
    html += """
<div class="lines-grid">
    <div class="card">
        <div class="card-title">Trading Performance</div>
        <table>
            <tr><td>Total Trades</td><td style="text-align:right;font-weight:700">{total}</td></tr>
            <tr><td>Win Rate</td><td style="text-align:right;font-weight:700">{wr:.1f}%</td></tr>
            <tr><td>Avg Win</td><td style="text-align:right;color:#16a34a">{aw:+.2f}%</td></tr>
            <tr><td>Avg Loss</td><td style="text-align:right;color:#cc0000">{al:+.2f}%</td></tr>
            <tr><td>Best Trade</td><td style="text-align:right;color:#16a34a">{best:+.2f}%</td></tr>
            <tr><td>Worst Trade</td><td style="text-align:right;color:#cc0000">{worst:+.2f}%</td></tr>
            <tr><td>Avg Hold</td><td style="text-align:right">{hold:.0f}h</td></tr>
        </table>
    </div>
    <div class="card">
        <div class="card-title">Decision Analysis</div>
        <table>
            <tr><td>Total Decisions</td><td style="text-align:right;font-weight:700">{decisions}</td></tr>
            <tr><td>Entries</td><td style="text-align:right">{entries}</td></tr>
            <tr><td>Exits</td><td style="text-align:right">{exits}</td></tr>
            <tr><td>Stance Updates</td><td style="text-align:right">{stances}</td></tr>
            <tr><td>Report-Triggered</td><td style="text-align:right">{report_t}</td></tr>
            <tr><td>Autonomous</td><td style="text-align:right">{auto_t}</td></tr>
            <tr><td>Overrides &#9889;</td><td style="text-align:right;font-weight:700">{overrides} ({override_rate:.0f}%)</td></tr>
        </table>
    </div>
</div>
""".format(
        total=ts.get('total', 0),
        wr=s['win_rate'],
        aw=ts.get('avg_win', 0),
        al=ts.get('avg_loss', 0),
        best=ts.get('best', 0),
        worst=ts.get('worst', 0),
        hold=ts.get('avg_hold_hours', 0),
        decisions=da.get('total_decisions', 0),
        entries=da.get('entries', 0),
        exits=da.get('exits', 0),
        stances=da.get('stance_updates', 0),
        report_t=da.get('report_triggered', 0),
        auto_t=da.get('autonomous', 0),
        overrides=ov.get('total', 0),
        override_rate=ov.get('override_rate', 0),
    )

    # Daily comparison table
    if data['daily']:
        html += '<div class="card"><div class="card-title">Daily Comparison</div>'
        html += '<table><tr><th>Date</th><th>Close</th><th>Active</th><th>Passive</th><th>Alpha</th><th>Stance</th><th>Report</th></tr>'

        for d in data['daily'][-30:]:
            a_pnl = d.get('active_pnl_pct', 0) or 0
            p_pnl = d.get('passive_pnl_pct', 0) or 0
            day_alpha = d.get('alpha_pct', 0) or 0
            report_marker = '&#128196;' if d.get('report_received') else ''

            html += """<tr>
                <td>{date}</td>
                <td>${close:.2f}</td>
                <td style="color:{ac}">{apnl:+.2f}%</td>
                <td style="color:{pc}">{ppnl:+.2f}%</td>
                <td style="color:{alc};font-weight:700">{alpha:+.2f}%</td>
                <td><span style="color:{sc}">{stance}</span></td>
                <td>{report}</td>
            </tr>""".format(
                date=d.get('date', ''),
                close=d.get('close_price', 0) or 0,
                ac=_pnl_color(a_pnl),
                apnl=a_pnl,
                pc=_pnl_color(p_pnl),
                ppnl=p_pnl,
                alc=_pnl_color(day_alpha),
                alpha=day_alpha,
                sc=_stance_color(d.get('active_stance', '')),
                stance=d.get('active_stance', ''),
                report=report_marker,
            )

        html += '</table></div>'

    # Report history
    if len(data['reports']) > 1:
        html += '<div class="card"><div class="card-title">Report History</div>'
        for r in data['reports'][:10]:
            r_stance = r.get('report_stance', 'N/A')
            r_conf = r.get('report_confidence', 0) or 0
            r_date = (r.get('published_date', '') or '')[:10]
            r_rationale = (r.get('report_rationale', '') or '')[:200]

            html += """
<div class="report-card" style="border-left-color:{sc};margin-bottom:8px">
    <strong>{date}</strong>
    <span class="stance-badge" style="background:{bg};color:{fg};margin-left:8px">{stance}</span>
    <span style="margin-left:6px;font-size:0.8em;color:#73788a">{conf:.0f}%</span>
    <div style="font-size:0.85em;color:#555;margin-top:4px">{rationale}</div>
</div>""".format(
                sc=_stance_color(r_stance),
                date=r_date,
                bg=_stance_bg(r_stance),
                fg=_stance_color(r_stance),
                stance=r_stance,
                conf=r_conf,
                rationale=r_rationale,
            )
        html += '</div>'

    # Footer
    html += """
<div class="footer">
    Company Watch &middot; {ticker} &middot; Generated {timestamp}<br>
    Active line: AI-managed trading | Passive line: Buy &amp; hold benchmark<br>
    <em>Paper trading only &middot; Not financial advice</em>
</div>
</div>
</body>
</html>""".format(ticker=WATCHED_TICKER, timestamp=timestamp)

    # Save report
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts_file = now.strftime('%Y%m%d_%H%M')

    latest_path = os.path.join(REPORTS_DIR, 'latest.html')
    with open(latest_path, 'w') as f:
        f.write(html)

    timestamped_path = os.path.join(REPORTS_DIR, 'companywatch_report_{}.html'.format(ts_file))
    with open(timestamped_path, 'w') as f:
        f.write(html)

    logger.info("Report generated: %s", latest_path)
    return latest_path


def _build_price_chart(price_history, active_pos, passive_pos):
    """Build an inline SVG sparkline chart showing both lines."""
    if not price_history or len(price_history) < 2:
        return '<div class="chart-area" style="text-align:center;padding-top:80px;color:#9ea2b0">Awaiting price data for chart</div>'

    prices = [p['price'] for p in price_history if p.get('price')]
    if not prices or len(prices) < 2:
        return '<div class="chart-area" style="text-align:center;padding-top:80px;color:#9ea2b0">Insufficient data</div>'

    # Chart dimensions
    w = 1040
    h = 180
    pad_x = 40
    pad_y = 20
    chart_w = w - 2 * pad_x
    chart_h = h - 2 * pad_y

    min_p = min(prices) * 0.999
    max_p = max(prices) * 1.001
    p_range = max_p - min_p if max_p > min_p else 1

    def x_pos(i):
        return pad_x + (i / (len(prices) - 1)) * chart_w

    def y_pos(p):
        return pad_y + chart_h - ((p - min_p) / p_range) * chart_h

    # Build price line
    points = ' '.join('{:.1f},{:.1f}'.format(x_pos(i), y_pos(p)) for i, p in enumerate(prices))

    # Entry line (if we have an active position)
    entry_line = ''
    if active_pos and active_pos.get('entry_price'):
        ey = y_pos(active_pos['entry_price'])
        entry_line = '<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="#0d7680" stroke-width="1" stroke-dasharray="6,4" opacity="0.6"/>'.format(
            pad_x, ey, w - pad_x, ey
        )
        entry_line += '<text x="{}" y="{:.1f}" font-size="10" fill="#0d7680" text-anchor="end">Entry ${:.2f}</text>'.format(
            w - pad_x - 4, ey - 4, active_pos['entry_price']
        )

    # Passive entry line
    passive_line = ''
    if passive_pos and passive_pos.get('entry_price'):
        py = y_pos(passive_pos['entry_price'])
        passive_line = '<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="#d97706" stroke-width="1" stroke-dasharray="4,4" opacity="0.5"/>'.format(
            pad_x, py, w - pad_x, py
        )
        passive_line += '<text x="{}" y="{:.1f}" font-size="10" fill="#d97706">Passive ${:.2f}</text>'.format(
            pad_x + 4, py - 4, passive_pos['entry_price']
        )

    svg = """
<div class="chart-area">
    <div class="card-title">Price Timeline (Last {hours}h)</div>
    <svg viewBox="0 0 {w} {h}" style="width:100%;height:auto">
        <rect x="{px}" y="{py}" width="{cw}" height="{ch}" fill="#fdf8f4" rx="4"/>
        {entry}
        {passive}
        <polyline points="{points}" fill="none" stroke="#0d7680" stroke-width="2"/>
        <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="#0d7680"/>
        <text x="{lx:.1f}" y="{ly:.1f}" font-size="11" fill="#33302e" font-weight="700">${last_p:.2f}</text>
        <text x="{px}" y="{h}" font-size="9" fill="#9ea2b0">${min:.2f}</text>
        <text x="{px}" y="{py2}" font-size="9" fill="#9ea2b0">${max:.2f}</text>
    </svg>
</div>""".format(
        hours=len(prices),
        w=w, h=h, px=pad_x, py=pad_y, cw=chart_w, ch=chart_h,
        entry=entry_line,
        passive=passive_line,
        points=points,
        last_x=x_pos(len(prices) - 1),
        last_y=y_pos(prices[-1]),
        lx=x_pos(len(prices) - 1) + 6,
        ly=y_pos(prices[-1]) + 4,
        last_p=prices[-1],
        min=min_p,
        max=max_p,
        py2=pad_y + 4,
    )

    return svg
