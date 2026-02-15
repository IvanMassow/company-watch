"""
Company Watch - Database Schema
Two parallel tracking lines:
  1. ACTIVE line: AI-managed trading decisions (buy/sell/hold)
  2. PASSIVE line: Buy-and-hold benchmark (never sells)
"""
import os
import sqlite3
import logging
from datetime import datetime, timezone

from config import DB_PATH, DATA_DIR

logger = logging.getLogger("companywatch.db")


def get_db():
    """Get database connection with WAL mode."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_db()

    # Stocks we're watching (could expand to multiple)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            company_name TEXT,
            exchange TEXT,
            sector TEXT,
            rss_url TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            is_active INTEGER DEFAULT 1
        )
    """)

    # Ingested reports from RSS
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            report_url TEXT UNIQUE,
            title TEXT,
            published_date TEXT,
            ingested_at TEXT DEFAULT (datetime('now')),
            rss_guid TEXT UNIQUE,

            -- Parsed from report
            report_stance TEXT,
            report_confidence REAL,
            report_rationale TEXT,
            report_watchpoints TEXT,
            report_risks TEXT,
            report_mispricing TEXT,

            -- Market context from report
            market_upside_pct REAL,
            market_sideways_pct REAL,
            market_downside_pct REAL,
            dominant_risk TEXT,
            dominant_upside TEXT,

            -- Full report content (for LLM analysis)
            report_html TEXT,
            report_text TEXT
        )
    """)

    # Active trading line - AI-managed positions
    # Each row = one position (buy/sell cycle)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS active_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,

            -- Position state
            state TEXT DEFAULT 'FLAT',
            direction TEXT,

            -- Entry
            entry_price REAL,
            entry_time TEXT,
            entry_reason TEXT,
            entry_report_id INTEGER REFERENCES reports(id),

            -- Exit
            exit_price REAL,
            exit_time TEXT,
            exit_reason TEXT,
            exit_report_id INTEGER REFERENCES reports(id),

            -- Current stance
            current_stance TEXT DEFAULT 'FADE',
            stance_confidence REAL DEFAULT 0,
            report_confidence REAL DEFAULT 0,
            house_confidence REAL DEFAULT 0,
            stance_updated_at TEXT,
            stance_report_id INTEGER REFERENCES reports(id),

            -- Duck-and-cover state
            is_ducking INTEGER DEFAULT 0,
            duck_exit_price REAL,
            duck_exit_time TEXT,
            duck_reason TEXT,

            -- Override tracking
            was_overridden INTEGER DEFAULT 0,
            override_reason TEXT,
            override_time TEXT,

            -- P&L
            realised_pnl_pct REAL,
            peak_price REAL,
            trough_price REAL,

            created_at TEXT DEFAULT (datetime('now')),
            closed_at TEXT
        )
    """)

    # Passive benchmark line - buy once, never sell
    conn.execute("""
        CREATE TABLE IF NOT EXISTS passive_position (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            entry_price REAL,
            entry_time TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)

    # Price snapshots - shared between both lines
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            price REAL,
            open_price REAL,
            high REAL,
            low REAL,
            volume REAL,
            change_pct REAL,

            -- Computed fields
            active_pnl_pct REAL,
            passive_pnl_pct REAL,
            active_state TEXT,

            UNIQUE(ticker, timestamp)
        )
    """)

    # Decision log - every decision the AI makes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            timestamp TEXT DEFAULT (datetime('now')),
            decision_type TEXT,

            -- What triggered this decision
            trigger TEXT,
            report_id INTEGER REFERENCES reports(id),

            -- The decision
            old_stance TEXT,
            new_stance TEXT,
            confidence REAL,
            report_confidence REAL,
            house_confidence REAL,
            reason TEXT,

            -- Market context at decision time
            price_at_decision REAL,
            spy_price REAL,
            spy_change_pct REAL,
            market_regime TEXT,

            -- DD details
            dd_type TEXT,
            dd_details TEXT,
            llm_analysis TEXT,

            -- Override info
            is_override INTEGER DEFAULT 0,
            override_what TEXT
        )
    """)

    # Daily summary - one row per day for charting
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,

            -- Prices
            open_price REAL,
            close_price REAL,
            high_price REAL,
            low_price REAL,

            -- Active line
            active_stance TEXT,
            active_pnl_pct REAL,
            active_cumulative_pnl REAL,
            active_position_held INTEGER DEFAULT 0,

            -- Passive line
            passive_pnl_pct REAL,
            passive_cumulative_pnl REAL,

            -- Report received?
            report_received INTEGER DEFAULT 0,
            report_stance TEXT,
            report_confidence REAL,

            -- Alpha (active - passive)
            alpha_pct REAL,

            UNIQUE(ticker, date)
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_time ON price_snapshots(ticker, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decision_log(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_ticker ON reports(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_ticker_date ON daily_summary(ticker, date)")

    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


def get_current_position(ticker):
    """Get the current active position for a ticker, or None if flat."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM active_positions WHERE ticker=? AND closed_at IS NULL ORDER BY id DESC LIMIT 1",
        (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_passive_position(ticker):
    """Get the passive (buy-and-hold) position for a ticker."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM passive_position WHERE ticker=? AND is_active=1",
        (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_latest_report(ticker):
    """Get the most recent report for a ticker."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM reports WHERE ticker=? ORDER BY published_date DESC LIMIT 1",
        (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_recent_decisions(ticker, limit=20):
    """Get recent decisions for a ticker."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM decision_log WHERE ticker=? ORDER BY timestamp DESC LIMIT ?",
        (ticker, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_daily_summaries(ticker, limit=90):
    """Get daily summaries for charting."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM daily_summary WHERE ticker=? ORDER BY date DESC LIMIT ?",
        (ticker, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def get_price_history(ticker, hours=96):
    """Get price snapshots for the last N hours."""
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM price_snapshots WHERE ticker=?
           AND timestamp >= datetime('now', ? || ' hours')
           ORDER BY timestamp ASC""",
        (ticker, str(-hours))
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
