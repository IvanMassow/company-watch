"""
Company Watch - Main Runner
Daemon that orchestrates the full system:
  1. Polls RSS for new daily reports (every 30 min) — all stocks
  2. Processes reports through the smart trader
  3. Tracks prices for both active and passive lines (every 60 min)
  4. Runs autonomous DD checks (every 2 hours)
  5. Generates comparison reports (every 6 hours or on change)
  6. Pushes to GitHub Pages

Supports multiple stocks — loops over WATCHED_STOCKS from config.

Usage:
    python3 runner.py          # Run as daemon
    python3 runner.py --once   # Single cycle
"""
import os
import sys
import time
import json
import signal
import logging
import subprocess
from datetime import datetime, timezone

from config import (
    SCAN_INTERVAL, TRACK_INTERVAL, DD_INTERVAL, REPORT_INTERVAL,
    WATCHED_TICKER, WATCHED_STOCKS, OPENAI_API_KEY, BASE_DIR, REPORTS_DIR,
    DUCK_COVER_ENABLED, DUCK_SELL_MINUTES_AFTER_OPEN, DUCK_REBUY_MINUTES_AFTER_OPEN,
    PREMARKET_DD_ENABLED,
)
from db import init_db, get_db, get_latest_report
from scanner import scan
from tracker import track_prices, ensure_passive_position, update_daily_summary
from trader import (
    process_new_report, autonomous_dd, is_market_open, get_or_create_position,
    premarket_dd, duck_and_cover_sell, duck_and_cover_rebuy,
    is_premarket_window, minutes_since_market_open,
)
from report_html import generate_html_report

# Set up logging
os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'logs', 'companywatch.log')),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("companywatch.runner")

# Graceful shutdown
_shutdown = False

def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Shutdown signal received (%s)", signum)
    _shutdown = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _all_tickers():
    """Get list of all ticker symbols."""
    return [s['ticker'] for s in WATCHED_STOCKS]


def export_dashboard_json(ticker=None):
    """Export a lightweight JSON summary for the Noah Dashboard.
    If ticker given, exports for that stock. Otherwise exports for default.
    """
    ticker = ticker or WATCHED_TICKER
    try:
        from analytics import generate_analytics
        data = generate_analytics(ticker=ticker)
        s = data['summary']

        # Latest daily change (last entry in daily summaries)
        daily_list = data.get('daily', [])
        latest_daily = daily_list[-1] if daily_list else {}
        daily_active_pnl = latest_daily.get('active_pnl_pct', 0) or 0
        daily_passive_pnl = latest_daily.get('passive_pnl_pct', 0) or 0

        dashboard = {
            'product': 'company_watch',
            'ticker': ticker,
            'generated_at': data['generated_at'],
            'current_price': s['current_price'],
            'active': {
                'state': s['active_state'],
                'direction': s['active_direction'],
                'entry_price': s['active_entry_price'],
                'unrealised_pnl': s['active_unrealised_pnl'],
                'realised_pnl': s['active_realised_pnl'],
                'total_pnl': s['active_total_pnl'],
                'daily_pnl': daily_active_pnl,
                'stance': None,
                'confidence': None,
            },
            'passive': {
                'entry_price': s['passive_entry_price'],
                'pnl': s['passive_pnl'],
                'daily_pnl': daily_passive_pnl,
            },
            'alpha': s['alpha'],
            'total_trades': s['total_trades'],
            'win_rate': s['win_rate'],
            'days_tracked': s['days_tracked'],
        }

        # Add current stance and dual confidence
        pos = data.get('active_position')
        if pos:
            dashboard['active']['stance'] = pos.get('current_stance')
            dashboard['active']['confidence'] = pos.get('stance_confidence')
            dashboard['active']['report_confidence'] = pos.get('report_confidence')
            dashboard['active']['house_confidence'] = pos.get('house_confidence')
            dashboard['active']['is_ducking'] = bool(pos.get('is_ducking'))

        summary_path = os.path.join(BASE_DIR, 'summary.json')
        with open(summary_path, 'w') as f:
            json.dump(dashboard, f, indent=2, default=str)

        logger.info("Dashboard JSON exported for %s", ticker)
    except Exception as e:
        logger.error("Dashboard export failed for %s: %s", ticker, e)


def generate_index_page():
    """Generate index.html — the stock picker landing page.
    If single stock: copies that stock's report as index.html.
    If multi-stock: generates a hub page listing all stocks.
    """
    if len(WATCHED_STOCKS) == 1:
        # Single stock — just copy latest report as index.html
        latest = os.path.join(REPORTS_DIR, 'latest.html')
        index = os.path.join(BASE_DIR, 'index.html')
        if os.path.exists(latest):
            with open(latest, 'r') as f:
                content = f.read()
            with open(index, 'w') as f:
                f.write(content)
        return

    # Multi-stock — build a hub page with stock picker
    from analytics import generate_analytics
    now = datetime.now(timezone.utc)
    timestamp = now.strftime('%Y-%m-%d %H:%M UTC')

    stock_cards = ''
    for st in WATCHED_STOCKS:
        try:
            data = generate_analytics(ticker=st['ticker'])
            s = data['summary']
            pos = data.get('active_position') or {}
            stance = pos.get('current_stance', 'FLAT')
            report_conf = pos.get('report_confidence', 0) or 0
            house_conf = pos.get('house_confidence', 0) or 0
            price = s['current_price']
            active_pnl = s['active_unrealised_pnl']
            passive_pnl = s['passive_pnl']
            alpha = s['alpha']
        except Exception:
            stance = 'FLAT'
            report_conf = 0
            house_conf = 0
            price = 0
            active_pnl = 0
            passive_pnl = 0
            alpha = 0

        stance_colors = {
            'BUY': ('#16a34a', '#dcfce7'), 'SELL': ('#cc0000', '#fef2f2'),
            'HOLD': ('#d97706', '#fef3c7'), 'FADE': ('#9ea2b0', '#f1f5f9'),
            'FLAT': ('#9ea2b0', '#f1f5f9'),
        }
        sc, sbg = stance_colors.get(stance, ('#9ea2b0', '#f1f5f9'))
        alpha_color = '#16a34a' if alpha > 0 else '#cc0000' if alpha < 0 else '#9ea2b0'

        stock_cards += """
        <a href="reports/{ticker}/latest.html" style="text-decoration:none;color:inherit">
        <div style="background:white;border-radius:10px;padding:24px;box-shadow:0 1px 4px rgba(0,0,0,0.06);border-top:4px solid #0d7680;transition:transform 0.15s">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div>
                    <div style="font-family:Montserrat,sans-serif;font-weight:700;font-size:1.4em;color:#0d7680">{ticker}</div>
                    <div style="font-size:0.8em;color:#73788a">{company}</div>
                </div>
                <span style="background:{sbg};color:{sc};padding:4px 14px;border-radius:20px;font-family:Montserrat,sans-serif;font-weight:700;font-size:0.85em">{stance}</span>
            </div>
            <div style="font-family:Montserrat,sans-serif;font-size:2em;font-weight:700;margin-bottom:8px">${price:.2f}</div>
            <div style="display:flex;gap:20px;font-size:0.85em">
                <div><span style="color:#73788a">Report:</span> <strong>{rconf:.0f}%</strong></div>
                <div><span style="color:#73788a">House:</span> <strong>{hconf:.0f}%</strong></div>
                <div><span style="color:#73788a">Alpha:</span> <strong style="color:{ac}">{alpha:+.2f}%</strong></div>
            </div>
        </div>
        </a>""".format(
            ticker=st['ticker'], company=st['company'],
            stance=stance, sc=sc, sbg=sbg,
            price=price, rconf=report_conf, hconf=house_conf,
            alpha=alpha, ac=alpha_color,
        )

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Company Watch | NOAH</title>
<!-- Open Graph / Social sharing preview -->
<meta property="og:type" content="website">
<meta property="og:title" content="NOAH Company Watch">
<meta property="og:description" content="Single stock intelligence. AI-managed trading vs buy-and-hold benchmark.">
<meta property="og:image" content="https://ivanmassow.github.io/company-watch/og-image.png">
<meta property="og:url" content="https://ivanmassow.github.io/company-watch/">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="NOAH Company Watch">
<meta name="twitter:description" content="Single stock intelligence. AI-managed trading vs buy-and-hold benchmark.">
<meta name="twitter:image" content="https://ivanmassow.github.io/company-watch/og-image.png">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=Lato:wght@300;400;700&family=Montserrat:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {{ --ink: #262a33; --grey-400: #9ea2b0; --paper: #FFF1E5; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Lato', sans-serif; background: var(--paper); color: var(--ink); padding-top: 56px; }}
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
}}
.nav-header .nav a:hover {{ color: #fff; }}
.nav-header .meta {{ margin-left: auto; color: var(--grey-400); font-size: 0.78rem; }}
.hero {{
    background: var(--ink); color: #fff;
    padding: 3rem 2rem 2.5rem; margin-top: -56px; padding-top: calc(56px + 2.5rem);
    text-align: center;
}}
.hero h1 {{
    font-family: 'Playfair Display', serif;
    font-size: clamp(2rem, 4.5vw, 3rem); font-weight: 700;
}}
.hero .subtitle {{
    font-size: 0.72rem; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: #FFA089; margin-bottom: 0.5rem;
}}
.container {{ max-width: 1120px; margin: 0 auto; padding: 0 2rem; }}
.stock-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 20px; margin: 32px 0;
}}
.stock-grid a div:hover {{ transform: translateY(-2px); }}
</style>
</head>
<body>
<div class="nav-header">
    <a href="https://ivanmassow.github.io/noah-dashboard/" class="logo">NOAH</a>
    <div class="nav">
        <a href="https://ivanmassow.github.io/polyhunter/">Poly Market</a>
        <a href="https://ivanmassow.github.io/hedgefund-tracker/">Hedge Fund</a>
        <a href="https://ivanmassow.github.io/company-watch/">Company Watch</a>
    </div>
    <div class="meta">Company Watch &middot; {count} Stock{plural}</div>
</div>

<div class="hero">
    <div class="container">
        <div class="subtitle">Company Watch &middot; Stock Intelligence</div>
        <h1>Select a Stock</h1>
        <div style="font-size:0.85rem;color:#d1d5db;margin-top:8px">{timestamp}</div>
    </div>
</div>

<div class="container">
    <div class="stock-grid">
        {cards}
    </div>
</div>

<footer style="background:var(--ink);padding:2rem;text-align:center;margin-top:3rem">
    <div style="font-family:Montserrat,sans-serif;font-weight:700;color:#fff;font-size:1rem;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px">NOAH</div>
    <p style="font-size:0.72rem;color:rgba(255,241,229,0.35)">
        Company Watch &mdash; stock intelligence system.
        <a href="https://ivanmassow.github.io/polyhunter/" style="color:rgba(255,241,229,0.5);text-decoration:none">Poly Market</a> &middot;
        <a href="https://ivanmassow.github.io/hedgefund-tracker/" style="color:rgba(255,241,229,0.5);text-decoration:none">Hedge Fund</a> &middot;
        <a href="https://ivanmassow.github.io/company-watch/" style="color:rgba(255,241,229,0.5);text-decoration:none">Company Watch</a>
    </p>
</footer>
</body>
</html>""".format(
        count=len(WATCHED_STOCKS),
        plural='s' if len(WATCHED_STOCKS) > 1 else '',
        timestamp=timestamp,
        cards=stock_cards,
    )

    index_path = os.path.join(BASE_DIR, 'index.html')
    with open(index_path, 'w') as f:
        f.write(html)
    logger.info("Index page generated with %d stocks", len(WATCHED_STOCKS))


def push_to_github():
    """Push latest reports, index, and summary to GitHub Pages."""
    try:
        # Generate index page (single stock = copy report, multi = hub page)
        generate_index_page()

        # Export dashboard JSON (for first/primary stock)
        export_dashboard_json()

        # Git operations
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        subprocess.run(
            ['git', 'add', 'index.html', 'reports/', 'summary.json'],
            cwd=BASE_DIR, capture_output=True, timeout=30
        )

        tickers_str = ','.join(_all_tickers())
        result = subprocess.run(
            ['git', 'commit', '-m', 'Update report {} {}'.format(tickers_str, now)],
            cwd=BASE_DIR, capture_output=True, timeout=30
        )

        if result.returncode == 0:
            subprocess.run(
                ['git', 'push'],
                cwd=BASE_DIR, capture_output=True, timeout=30
            )
            logger.info("Pushed to GitHub")
        else:
            stderr = result.stderr.decode() if result.stderr else ''
            if 'nothing to commit' in stderr:
                logger.debug("Nothing to commit")
            else:
                logger.warning("Git commit issue: %s", stderr[:200])

    except subprocess.TimeoutExpired:
        logger.error("Git push timed out")
    except Exception as e:
        logger.error("GitHub push failed: %s", e)


def _run_stock_cycle(ticker, llm_trader):
    """Run a single cycle for one stock: scan, process, track, DD."""
    stock = None
    for s in WATCHED_STOCKS:
        if s['ticker'] == ticker:
            stock = s
            break

    # Ensure positions exist
    ensure_passive_position(ticker=ticker)
    get_or_create_position(ticker)

    # 1. Scan for new reports
    rss_url = stock['rss_url'] if stock else None
    company = stock['company'] if stock else None
    new_reports = scan(ticker=ticker, company=company, rss_url=rss_url)

    # 2. Process new reports through trader
    if new_reports:
        report = get_latest_report(ticker)
        if report:
            process_new_report(report, llm_trader)

    # 3. Track prices
    track_prices(ticker=ticker)

    # 4. Pre-market DD
    if PREMARKET_DD_ENABLED and is_premarket_window():
        premarket_dd(llm_trader)

    # 5. Duck-and-cover phases
    if DUCK_COVER_ENABLED and is_market_open():
        mins = minutes_since_market_open()
        if DUCK_SELL_MINUTES_AFTER_OPEN <= mins <= DUCK_SELL_MINUTES_AFTER_OPEN + 10:
            duck_and_cover_sell(llm_trader)
        if DUCK_REBUY_MINUTES_AFTER_OPEN <= mins <= DUCK_REBUY_MINUTES_AFTER_OPEN + 10:
            duck_and_cover_rebuy(llm_trader)

    # 6. Autonomous DD
    if is_market_open():
        autonomous_dd(llm_trader)

    # 7. Update daily summary
    update_daily_summary(ticker=ticker)

    # 8. Generate per-stock report
    generate_html_report(ticker=ticker)


def run_once():
    """Run a single complete cycle for all stocks."""
    logger.info("=== Company Watch: Single Cycle Start (%d stocks) ===", len(WATCHED_STOCKS))

    # Initialize
    init_db()

    # Set up LLM trader if API key available
    llm_trader = None
    if OPENAI_API_KEY:
        import llm_trader as llm_mod
        llm_trader = llm_mod
        logger.info("LLM DD enabled (OpenAI)")

    # Process each stock
    for stock in WATCHED_STOCKS:
        logger.info("--- Processing %s (%s) ---", stock['ticker'], stock['company'])
        _run_stock_cycle(stock['ticker'], llm_trader)

    # Push everything
    push_to_github()

    logger.info("=== Single Cycle Complete ===")


def run():
    """Run as a continuous daemon."""
    logger.info("=== Company Watch Daemon Starting (%d stocks: %s) ===",
                len(WATCHED_STOCKS), ', '.join(_all_tickers()))

    init_db()

    llm_trader = None
    if OPENAI_API_KEY:
        import llm_trader as llm_mod
        llm_trader = llm_mod
        logger.info("LLM DD enabled (OpenAI)")

    # Initial cycle for all stocks
    for stock in WATCHED_STOCKS:
        _run_stock_cycle(stock['ticker'], llm_trader)
    push_to_github()

    # Timing
    last_scan = time.time()
    last_track = time.time()
    last_dd = time.time()
    last_report = time.time()

    logger.info("Daemon running. Intervals: scan=%dm track=%dm dd=%dm report=%dm",
                SCAN_INTERVAL // 60, TRACK_INTERVAL // 60,
                DD_INTERVAL // 60, REPORT_INTERVAL // 60)

    while not _shutdown:
        now = time.time()
        changed = False

        # Scan for new reports (all stocks)
        if now - last_scan >= SCAN_INTERVAL:
            new = scan()  # scans all stocks when no params
            last_scan = now
            if new:
                # Process reports for each stock that might have new ones
                for stock in WATCHED_STOCKS:
                    report = get_latest_report(stock['ticker'])
                    if report:
                        process_new_report(report, llm_trader)
                changed = True

        # Track prices (all stocks)
        if now - last_track >= TRACK_INTERVAL:
            for stock in WATCHED_STOCKS:
                track_prices(ticker=stock['ticker'])
                update_daily_summary(ticker=stock['ticker'])
            last_track = now
            changed = True

        # Pre-market DD (before NYSE opens)
        if PREMARKET_DD_ENABLED and is_premarket_window():
            if now - last_dd >= DD_INTERVAL:
                premarket_dd(llm_trader)
                last_dd = now
                changed = True

        # Duck-and-cover phases (market open timing)
        if DUCK_COVER_ENABLED and is_market_open():
            mins = minutes_since_market_open()
            if DUCK_SELL_MINUTES_AFTER_OPEN <= mins <= DUCK_SELL_MINUTES_AFTER_OPEN + 10:
                duck_and_cover_sell(llm_trader)
                changed = True
            if DUCK_REBUY_MINUTES_AFTER_OPEN <= mins <= DUCK_REBUY_MINUTES_AFTER_OPEN + 10:
                duck_and_cover_rebuy(llm_trader)
                changed = True

        # Autonomous DD (during market hours)
        if now - last_dd >= DD_INTERVAL:
            if is_market_open():
                autonomous_dd(llm_trader)
            last_dd = now

        # Generate reports (all stocks)
        if now - last_report >= REPORT_INTERVAL or changed:
            for stock in WATCHED_STOCKS:
                generate_html_report(ticker=stock['ticker'])
            push_to_github()
            last_report = now

        # Sleep in small increments for signal responsiveness
        for _ in range(60):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("=== Company Watch Daemon Stopped ===")


if __name__ == '__main__':
    if '--once' in sys.argv:
        run_once()
    else:
        run()
