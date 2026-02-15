"""
Company Watch - Configuration
Stock intelligence system that tracks daily reports,
makes trading decisions, and compares against a passive hold.

Supports multiple stocks — each stock has its own RSS feed, ticker,
and tracking line. Add a new stock by adding an entry to WATCHED_STOCKS.
"""
import os
import json

# ====================================================================
# MULTI-STOCK CONFIGURATION
# Each stock is a dict with: ticker, company, rss_url
# Add a new stock = add an entry here. That's it.
# ====================================================================
_stocks_json = os.environ.get("WATCHED_STOCKS", "")

if _stocks_json:
    # From env: JSON array of stock dicts
    WATCHED_STOCKS = json.loads(_stocks_json)
else:
    # Default: build from individual env vars (backward compatible)
    WATCHED_STOCKS = [
        {
            "ticker": os.environ.get("WATCHED_TICKER", "BABA"),
            "company": os.environ.get("WATCHED_COMPANY", "Alibaba"),
            "rss_url": os.environ.get("RSS_URL", "https://alibaba.makes.news/rss.xml"),
        },
    ]

# Convenience shortcuts for backward compatibility (first stock)
WATCHED_TICKER = WATCHED_STOCKS[0]["ticker"]
WATCHED_COMPANY = WATCHED_STOCKS[0]["company"]
RSS_URL = WATCHED_STOCKS[0]["rss_url"]

# Alpha Vantage (price data)
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"
AV_RATE_LIMIT = 12  # seconds between calls (5/min on free tier)

# OpenAI (LLM due diligence)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Intervals (seconds)
SCAN_INTERVAL = 30 * 60        # Check RSS every 30 minutes
TRACK_INTERVAL = 60 * 60       # Fetch prices every 60 minutes
DD_INTERVAL = 2 * 60 * 60      # Run autonomous DD checks every 2 hours
REPORT_INTERVAL = 6 * 60 * 60  # Heartbeat report every 6 hours

# Trading windows (UTC hours) - NYSE opens 14:30 UTC (9:30 ET)
MARKET_OPEN_UTC = 14.5   # 14:30
MARKET_CLOSE_UTC = 21.0  # 21:00
MARKET_DAYS = [0, 1, 2, 3, 4]  # Monday-Friday

# Hong Kong market hours (UTC) for pre-market checks on dual-listed stocks
HK_MARKET_OPEN_UTC = 1.5    # 09:30 HKT = 01:30 UTC
HK_MARKET_CLOSE_UTC = 8.0   # 16:00 HKT = 08:00 UTC

# Stance options (from architecture)
STANCES = ["BUY", "SELL", "HOLD", "FADE"]

# Confidence thresholds
CONFIDENCE_ACT = 65      # >= this to BUY or SELL
CONFIDENCE_WATCH = 45    # >= this to HOLD, below = FADE

# Profit-taking thresholds
PROFIT_TAKE_PCT = 15.0          # Consider taking profit at +15%
PROFIT_TAKE_STRONG_PCT = 25.0   # Strongly consider at +25%
LOSS_STOP_PCT = -10.0           # Consider cutting at -10%
LOSS_STOP_HARD_PCT = -15.0      # Hard stop at -15%

# Drawdown from peak triggers profit protection
DRAWDOWN_FROM_PEAK_PCT = 5.0    # If we've pulled back 5% from peak, protect gains

# Autonomous override thresholds
# The system can override report advice if it detects these conditions
OVERRIDE_PRICE_MOVE_PCT = 8.0     # >8% move since report = check if horse bolted
OVERRIDE_MARKET_CRASH_PCT = -3.0  # S&P down >3% = market crash override

# Duck-and-cover: sell before a known storm, rebuy after the wind passes
DUCK_COVER_ENABLED = True
DUCK_SELL_MINUTES_AFTER_OPEN = 0    # Sell at market open (9:30 ET / 14:30 UTC)
DUCK_REBUY_MINUTES_AFTER_OPEN = 60  # Re-buy at 10:30 ET / 15:30 UTC
DUCK_MIN_CONFIDENCE = 60            # Only duck-and-cover if we'd re-enter (report conf >= this)

# Pre-market DD: before NYSE opens, check HK tape and overnight news
PREMARKET_DD_ENABLED = True
PREMARKET_WINDOW_HOURS_BEFORE_OPEN = 2.0  # Start pre-market DD 2h before NYSE open (12:30 UTC)

# Tracking
TRACKING_WINDOW_HOURS = 720  # 30 days - much longer than hedgefund tracker
MAX_HISTORY_DAYS = 365       # Keep up to a year of data

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(DATA_DIR, "companywatch.db")
