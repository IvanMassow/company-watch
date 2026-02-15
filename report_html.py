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
from config import REPORTS_DIR, WATCHED_TICKER, WATCHED_STOCKS

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


def generate_html_report(ticker=None):
    """Generate the full HTML report and save to reports directory.
    If ticker given, generates for that stock. Otherwise uses default.
    Reports go into reports/<TICKER>/latest.html when multi-stock.
    """
    ticker = ticker or WATCHED_TICKER
    data = generate_analytics(ticker=ticker)
    s = data['summary']

    now = datetime.now(timezone.utc)
    timestamp = now.strftime('%Y-%m-%d %H:%M UTC')

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Company Watch: {ticker} | {timestamp}</title>
<!-- Open Graph / Social sharing preview -->
<meta property="og:type" content="website">
<meta property="og:title" content="NOAH Company Watch - {ticker}">
<meta property="og:description" content="Single stock intelligence. AI-managed trading vs buy-and-hold benchmark.">
<meta property="og:image" content="https://ivanmassow.github.io/company-watch/og-image.png">
<meta property="og:url" content="https://ivanmassow.github.io/company-watch/">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="NOAH Company Watch - {ticker}">
<meta name="twitter:description" content="Single stock intelligence. AI-managed trading vs buy-and-hold benchmark.">
<meta name="twitter:image" content="https://ivanmassow.github.io/company-watch/og-image.png">
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=Lato:wght@300;400;700&family=Montserrat:wght@500;700&display=swap');

:root {{
    --ink: #262a33;
    --ink-light: #3d424d;
    --ink-subtle: #73788a;
    --grey-300: #d1d5db;
    --grey-400: #9ea2b0;
    --paper: #FFF1E5;
    --accent: #0d7680;
    --accent-light: #1a9ba5;
    --green: #16a34a;
    --red: #cc0000;
    --gold: #d97706;
    --blush: #ffe4d6;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: 'Lato', sans-serif;
    background: var(--paper);
    color: var(--ink);
    line-height: 1.6;
    padding-top: 56px;
}}

.container {{
    max-width: 1120px;
    margin: 0 auto;
    padding: 0 2rem;
}}

/* Fixed NOAH header bar */
.nav-header {{
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    background: var(--ink); height: 56px;
    display: flex; align-items: center; padding: 0 2rem;
}}
.nav-header .logo {{
    font-family: 'Montserrat', sans-serif; font-weight: 700;
    color: #fff; font-size: 1.3rem; letter-spacing: 0.08em;
    text-transform: uppercase; text-decoration: none;
}}
.nav-header .nav {{ display: flex; gap: 1.5rem; margin-left: 3rem; }}
.nav-header .nav a {{
    color: var(--grey-400); text-decoration: none;
    font-size: 0.82rem; letter-spacing: 0.04em;
    transition: color 0.2s;
}}
.nav-header .nav a:hover {{ color: #fff; }}
.nav-header .nav a.active {{ color: #fff; }}
.nav-header .meta {{
    margin-left: auto; color: var(--grey-400);
    font-size: 0.78rem; letter-spacing: 0.02em;
}}

/* Hero */
.hero {{
    background: var(--ink); color: #fff;
    padding: 3rem 2rem 2.5rem;
    margin-top: -56px; padding-top: calc(56px + 2.5rem);
}}
.hero .subtitle {{
    font-size: 0.72rem; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: #FFA089; margin-bottom: 0.5rem;
}}
.hero h1 {{
    font-family: 'Playfair Display', serif;
    font-size: clamp(2rem, 4.5vw, 3rem); font-weight: 700;
    letter-spacing: -0.01em; margin-bottom: 0.5rem;
}}
.hero .price-hero {{
    font-family: 'Montserrat', sans-serif;
    font-size: 2.8em;
    font-weight: 700;
    margin: 12px 0 6px;
}}
.hero .price-change {{
    font-size: 1.1em;
    color: var(--grey-300);
}}
.hero .hero-meta {{
    font-size: 0.78rem;
    color: var(--grey-400);
    margin-top: 12px;
}}

/* Section headers */
.act-header {{
    font-family: 'Playfair Display', serif;
    font-size: 1.4em;
    color: #0d7680;
    border-bottom: 2px solid #0d7680;
    padding-bottom: 6px;
    margin: 32px 0 16px;
    scroll-margin-top: 72px;
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

/* Responsive */
@media (max-width: 768px) {{
    .lines-grid {{ grid-template-columns: 1fr; }}
    .hero h1 {{ font-size: 1.6em; }}
    .hero .price-hero {{ font-size: 2em; }}
    .nav-header {{ padding: 0 1rem; }}
    .nav-header .nav {{ margin-left: 1.5rem; gap: 0.8rem; }}
    .nav-header .meta {{ display: none; }}
    .container {{ padding: 0 1rem; }}
}}
</style>
</head>
<body>

<!-- NOAH Header Bar -->
<div class="nav-header">
    <a href="https://ivanmassow.github.io/noah-dashboard/" class="logo">NOAH</a>
    <div class="nav">
        <a href="#arena">Arena</a>
        <a href="#ledger">Ledger</a>
        <a href="#scoreboard">Scoreboard</a>
        <span style="color:rgba(255,255,255,0.15)">|</span>
        <a href="https://ivanmassow.github.io/polyhunter/">Poly Market</a>
        <a href="https://ivanmassow.github.io/hedgefund-tracker/">Hedge Fund</a>
        <a href="https://ivanmassow.github.io/company-watch/">Company Watch</a>
    </div>
    <div class="meta">Company Watch &middot; {ticker}</div>
</div>
""".format(ticker=ticker, timestamp=timestamp)

    # === HERO with stock picker dropdown ===
    change_pct = data['latest_price'].get('change_pct', 0) if data['latest_price'] else 0
    change_arrow = _pnl_arrow(change_pct)
    change_color = _pnl_color(change_pct)

    # Build stock picker if more than one stock
    stock_picker = ''
    if len(WATCHED_STOCKS) > 1:
        picker_items = ''
        for st in WATCHED_STOCKS:
            active_cls = ' style="color:#fff;font-weight:700"' if st['ticker'] == ticker else ''
            picker_items += '<a href="../{t}/latest.html" style="display:block;padding:6px 16px;color:var(--grey-300);text-decoration:none;font-size:0.85rem;transition:color 0.2s"{cls}>{t} &middot; {c}</a>'.format(
                t=st['ticker'], c=st['company'], cls=active_cls)
        stock_picker = """
        <div style="position:relative;float:right;margin-top:-20px" class="stock-picker">
            <button onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='block'?'none':'block'"
                style="background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.2);color:#fff;padding:8px 16px;border-radius:6px;cursor:pointer;font-family:Montserrat,sans-serif;font-size:0.8rem;font-weight:700;letter-spacing:0.05em">
                {current} &#9662;
            </button>
            <div style="display:none;position:absolute;right:0;top:100%;margin-top:4px;background:var(--ink);border:1px solid rgba(255,255,255,0.15);border-radius:6px;min-width:200px;padding:6px 0;z-index:50;box-shadow:0 8px 24px rgba(0,0,0,0.3)">
                {items}
            </div>
        </div>""".format(current=ticker, items=picker_items)

    html += """
<div class="hero">
    <div class="container">
        {picker}
        <div class="subtitle">Company Watch &middot; Stock Intelligence</div>
        <h1>{ticker}</h1>
        <div class="price-hero">${price:.2f}</div>
        <div class="price-change" style="color:{cc}">{arrow} {change:+.2f}% today</div>
        <div class="hero-meta">{timestamp}</div>
    </div>
</div>

<div class="container">
""".format(
        picker=stock_picker,
        ticker=ticker,
        price=s['current_price'],
        cc=change_color,
        arrow=change_arrow,
        change=change_pct,
        timestamp=timestamp,
    )

    # === ACT 1: THE ARENA ===
    html += '<h2 class="act-header" id="arena">Act I: The Arena</h2>'

    # Current stance badge
    stance = s.get('active_state', 'FLAT')
    stance_label = stance
    if s.get('active_direction'):
        stance_label = s['active_direction'] + ' (' + stance + ')'

    active_pos = data.get('active_position')
    stance_from_pos = 'FADE'
    stance_conf = 0
    report_conf = 0
    house_conf = 0
    is_ducking = False
    if active_pos:
        stance_from_pos = active_pos.get('current_stance', 'FADE')
        stance_conf = active_pos.get('stance_confidence', 0) or 0
        report_conf = active_pos.get('report_confidence', 0) or 0
        house_conf = active_pos.get('house_confidence', 0) or 0
        is_ducking = bool(active_pos.get('is_ducking'))

    # Dual confidence: report vs house
    conf_diff = house_conf - report_conf
    if conf_diff > 5:
        conf_verdict = 'House amplified'
        conf_icon = '&#9650;'  # up
        conf_verdict_color = '#16a34a'
    elif conf_diff < -5:
        conf_verdict = 'House knocked down'
        conf_icon = '&#9660;'  # down
        conf_verdict_color = '#cc0000'
    else:
        conf_verdict = 'House agrees'
        conf_icon = '&#9644;'  # flat
        conf_verdict_color = '#73788a'

    duck_badge = ''
    if is_ducking:
        duck_badge = ' <span style="background:#fef3c7;color:#d97706;padding:3px 10px;border-radius:12px;font-size:0.75em;font-weight:700;margin-left:8px">DUCK &amp; COVER</span>'

    html += """
<div class="card">
    <div class="card-title">Current Stance</div>
    <div style="margin-bottom:12px">
        <span class="stance-badge" style="background:{bg};color:{fg}">{stance}</span>
        {duck}
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap">
        <div style="text-align:center;min-width:100px">
            <div style="font-family:Montserrat,sans-serif;font-size:1.6em;font-weight:700;color:#0d7680">{report_conf:.0f}%</div>
            <div style="font-size:0.75em;color:#73788a;text-transform:uppercase;letter-spacing:0.05em">Report</div>
        </div>
        <div style="text-align:center;min-width:100px">
            <div style="font-family:Montserrat,sans-serif;font-size:1.6em;font-weight:700;color:#262a33">{house_conf:.0f}%</div>
            <div style="font-size:0.75em;color:#73788a;text-transform:uppercase;letter-spacing:0.05em">House</div>
        </div>
        <div style="text-align:center;min-width:120px;padding-top:4px">
            <div style="font-size:0.9em;color:{vc}">{vi} {verdict}</div>
            <div style="font-size:0.75em;color:#73788a;margin-top:2px">Effective: {eff:.0f}%</div>
        </div>
    </div>
</div>
""".format(
        stance=stance_from_pos,
        bg=_stance_bg(stance_from_pos),
        fg=_stance_color(stance_from_pos),
        duck=duck_badge,
        report_conf=report_conf,
        house_conf=house_conf,
        vc=conf_verdict_color,
        vi=conf_icon,
        verdict=conf_verdict,
        eff=stance_conf,
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
    <span style="margin-left:8px;font-size:0.85em">Report Confidence: {conf:.0f}%</span>
    <span style="margin-left:8px;font-size:0.85em;color:#0d7680">|</span>
    <span style="margin-left:8px;font-size:0.85em">House: {hconf:.0f}%</span>
    <p style="margin-top:8px;font-size:0.9em;color:#555">{rationale}</p>
</div>
""".format(
            sc=_stance_color(r_stance),
            date=r_date,
            bg=_stance_bg(r_stance),
            fg=_stance_color(r_stance),
            stance=r_stance,
            conf=r_conf,
            hconf=house_conf,
            rationale=r_rationale[:300],
        )

    # === ACT 2: THE LEDGER ===
    html += '<h2 class="act-header" id="ledger">Act II: The Ledger</h2>'

    # Decision log table
    html += '<div class="card"><div class="card-title">Recent Decisions</div>'
    html += '<table><tr><th>Time</th><th>Type</th><th>From</th><th>To</th><th>Report</th><th>House</th><th>Trigger</th><th>Reason</th></tr>'

    for d in data['decisions'][:15]:
        ts = (d.get('timestamp', '') or '')[:16]
        override_marker = ' &#9889;' if d.get('is_override') else ''
        r_conf = d.get('report_confidence')
        h_conf = d.get('house_confidence')
        html += """<tr>
            <td>{ts}</td>
            <td>{dtype}{override}</td>
            <td><span style="color:{ofc}">{old}</span></td>
            <td><span style="color:{nfc}">{new}</span></td>
            <td>{rconf}</td>
            <td>{hconf}</td>
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
            rconf='{:.0f}%'.format(r_conf) if r_conf else '',
            hconf='{:.0f}%'.format(h_conf) if h_conf else '',
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
    html += '<h2 class="act-header" id="scoreboard">Act III: The Scoreboard</h2>'

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

    # Footer (NOAH style)
    html += """
</div><!-- end container -->

<footer style="background:var(--ink);padding:2rem;text-align:center;margin-top:3rem">
    <a href="https://ivanmassow.github.io/noah-dashboard/" style="text-decoration:none"><div style="font-family:Montserrat,sans-serif;font-weight:700;color:#fff;font-size:1rem;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px">NOAH</div></a>
    <p style="font-size:0.82rem;color:rgba(255,241,229,0.5);margin-bottom:8px">
        Company Watch &mdash; single stock intelligence tracking {ticker}.
        Active line: AI-managed trading | Passive line: Buy &amp; hold benchmark.
    </p>
    <p style="font-size:0.72rem;color:rgba(255,241,229,0.35);margin-bottom:8px">
        <a href="https://ivanmassow.github.io/polyhunter/" style="color:rgba(255,241,229,0.5);text-decoration:none">Poly Market</a> &middot;
        <a href="https://ivanmassow.github.io/hedgefund-tracker/" style="color:rgba(255,241,229,0.5);text-decoration:none">Hedge Fund</a> &middot;
        <a href="https://ivanmassow.github.io/company-watch/" style="color:rgba(255,241,229,0.5);text-decoration:none">Company Watch</a>
    </p>
    <p style="font-size:0.72rem;color:rgba(255,241,229,0.25)">
        Report generated {timestamp}.
    </p>
    <div style="margin-top:1.2rem;max-width:560px;margin-left:auto;margin-right:auto;padding:0.8rem 1rem;border-top:1px solid rgba(255,241,229,0.12)">
        <p style="font-size:0.7rem;color:rgba(255,241,229,0.55);line-height:1.7;text-align:center;margin:0">
            <strong style="color:rgba(255,241,229,0.7);letter-spacing:0.08em;text-transform:uppercase;font-size:0.65rem">Disclaimer</strong><br>
            You are welcome to view these pages. The trading algorithms and analysis presented here are experimental and under active development. Nothing on this site constitutes financial advice. We accept no responsibility for any losses incurred from acting on information found here. These pages are intended for internal research purposes. You are strongly advised to conduct your own due diligence before making any investment decisions.
        </p>
    </div>
</footer>

</body>
</html>""".format(ticker=ticker, timestamp=timestamp)

    # Save report — per-stock subdirectory when multi-stock
    ts_file = now.strftime('%Y%m%d_%H%M')

    if len(WATCHED_STOCKS) > 1:
        # Multi-stock: reports/<TICKER>/latest.html
        stock_dir = os.path.join(REPORTS_DIR, ticker)
        os.makedirs(stock_dir, exist_ok=True)
        latest_path = os.path.join(stock_dir, 'latest.html')
        timestamped_path = os.path.join(stock_dir, 'report_{}.html'.format(ts_file))
    else:
        # Single stock: reports/latest.html (backward compatible)
        os.makedirs(REPORTS_DIR, exist_ok=True)
        latest_path = os.path.join(REPORTS_DIR, 'latest.html')
        timestamped_path = os.path.join(REPORTS_DIR, 'companywatch_report_{}.html'.format(ts_file))

    with open(latest_path, 'w') as f:
        f.write(html)
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
        <text x="{lx:.1f}" y="{ly:.1f}" font-size="11" fill="#262a33" font-weight="700">${last_p:.2f}</text>
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
