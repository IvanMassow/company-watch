"""
Company Watch - Main Runner
Daemon that orchestrates the full system:
  1. Polls RSS for new daily reports (every 30 min)
  2. Processes reports through the smart trader
  3. Tracks prices for both active and passive lines (every 60 min)
  4. Runs autonomous DD checks (every 2 hours)
  5. Generates comparison reports (every 6 hours or on change)
  6. Pushes to GitHub Pages

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
    WATCHED_TICKER, OPENAI_API_KEY, BASE_DIR, REPORTS_DIR,
)
from db import init_db, get_db, get_latest_report
from scanner import scan
from tracker import track_prices, ensure_passive_position, update_daily_summary
from trader import process_new_report, autonomous_dd, is_market_open, get_or_create_position
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


def export_dashboard_json():
    """Export a lightweight JSON summary for the Noah Dashboard."""
    try:
        from analytics import generate_analytics
        data = generate_analytics()
        s = data['summary']

        dashboard = {
            'product': 'company_watch',
            'ticker': WATCHED_TICKER,
            'generated_at': data['generated_at'],
            'current_price': s['current_price'],
            'active': {
                'state': s['active_state'],
                'direction': s['active_direction'],
                'entry_price': s['active_entry_price'],
                'unrealised_pnl': s['active_unrealised_pnl'],
                'realised_pnl': s['active_realised_pnl'],
                'total_pnl': s['active_total_pnl'],
                'stance': None,
                'confidence': None,
            },
            'passive': {
                'entry_price': s['passive_entry_price'],
                'pnl': s['passive_pnl'],
            },
            'alpha': s['alpha'],
            'total_trades': s['total_trades'],
            'win_rate': s['win_rate'],
            'days_tracked': s['days_tracked'],
        }

        # Add current stance
        pos = data.get('active_position')
        if pos:
            dashboard['active']['stance'] = pos.get('current_stance')
            dashboard['active']['confidence'] = pos.get('stance_confidence')

        summary_path = os.path.join(BASE_DIR, 'summary.json')
        with open(summary_path, 'w') as f:
            json.dump(dashboard, f, indent=2, default=str)

        logger.info("Dashboard JSON exported")
    except Exception as e:
        logger.error("Dashboard export failed: %s", e)


def push_to_github():
    """Push latest report and summary to GitHub Pages."""
    try:
        # Copy latest report to index.html
        latest = os.path.join(REPORTS_DIR, 'latest.html')
        index = os.path.join(BASE_DIR, 'index.html')

        if os.path.exists(latest):
            with open(latest, 'r') as f:
                content = f.read()
            with open(index, 'w') as f:
                f.write(content)

        # Export dashboard JSON
        export_dashboard_json()

        # Git operations
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        subprocess.run(
            ['git', 'add', 'index.html', 'reports/latest.html', 'summary.json'],
            cwd=BASE_DIR, capture_output=True, timeout=30
        )

        result = subprocess.run(
            ['git', 'commit', '-m', 'Update report {} {}'.format(WATCHED_TICKER, now)],
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


def run_once():
    """Run a single complete cycle."""
    logger.info("=== Company Watch: Single Cycle Start (%s) ===", WATCHED_TICKER)

    # Initialize
    init_db()

    # Set up LLM trader if API key available
    llm_trader = None
    if OPENAI_API_KEY:
        import llm_trader as llm_mod
        llm_trader = llm_mod
        logger.info("LLM DD enabled (OpenAI)")

    # Ensure passive position exists
    ensure_passive_position()

    # Ensure active position exists (even if FLAT)
    get_or_create_position(WATCHED_TICKER)

    # 1. Scan for new reports
    new_reports = scan()

    # 2. Process new reports through trader
    if new_reports:
        report = get_latest_report(WATCHED_TICKER)
        if report:
            process_new_report(report, llm_trader)

    # 3. Track prices
    track_prices()

    # 4. Run autonomous DD (if holding a position)
    if is_market_open():
        autonomous_dd(llm_trader)

    # 5. Update daily summary
    update_daily_summary()

    # 6. Generate report
    generate_html_report()

    # 7. Push to GitHub
    push_to_github()

    logger.info("=== Single Cycle Complete ===")


def run():
    """Run as a continuous daemon."""
    logger.info("=== Company Watch Daemon Starting (%s) ===", WATCHED_TICKER)

    init_db()

    llm_trader = None
    if OPENAI_API_KEY:
        import llm_trader as llm_mod
        llm_trader = llm_mod
        logger.info("LLM DD enabled (OpenAI)")

    # Ensure positions exist
    ensure_passive_position()
    get_or_create_position(WATCHED_TICKER)

    # Initial scan and track
    new_reports = scan()
    if new_reports:
        report = get_latest_report(WATCHED_TICKER)
        if report:
            process_new_report(report, llm_trader)

    track_prices()
    update_daily_summary()
    generate_html_report()
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

        # Scan for new reports
        if now - last_scan >= SCAN_INTERVAL:
            new = scan()
            last_scan = now
            if new:
                report = get_latest_report(WATCHED_TICKER)
                if report:
                    process_new_report(report, llm_trader)
                changed = True

        # Track prices
        if now - last_track >= TRACK_INTERVAL:
            track_prices()
            update_daily_summary()
            last_track = now
            changed = True

        # Autonomous DD
        if now - last_dd >= DD_INTERVAL:
            if is_market_open():
                autonomous_dd(llm_trader)
            last_dd = now

        # Generate report
        if now - last_report >= REPORT_INTERVAL or changed:
            generate_html_report()
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
