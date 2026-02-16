"""
Company Watch - RSS Scanner & Report Parser
Polls RSS feed for new Company Watch reports, parses stance/confidence/rationale,
and ingests into the database.
"""
import re
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests

from config import RSS_URL, WATCHED_TICKER, WATCHED_COMPANY, WATCHED_STOCKS
from db import get_db

logger = logging.getLogger("companywatch.scanner")


class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and extract plain text."""
    def __init__(self):
        super().__init__()
        self.result = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = False
        if tag in ('p', 'br', 'div', 'h1', 'h2', 'h3', 'h4', 'li', 'tr'):
            self.result.append('\n')

    def handle_data(self, data):
        if not self._skip:
            self.result.append(data)

    def get_text(self):
        return ''.join(self.result).strip()


def html_to_text(html):
    """Convert HTML to plain text."""
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def fetch_rss(rss_url=None, ticker=None, company=None):
    """Fetch RSS feed and return items relevant to our watched stock.
    Accepts optional params for multi-stock support.
    """
    rss_url = rss_url or RSS_URL
    ticker = ticker or WATCHED_TICKER
    company = company or WATCHED_COMPANY

    try:
        resp = requests.get(rss_url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("RSS fetch failed for %s: %s", ticker, e)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error("RSS parse failed for %s: %s", ticker, e)
        return []

    items = []
    for item in root.findall('.//item'):
        title = item.findtext('title', '').strip()
        link = item.findtext('link', '').strip()
        desc = item.findtext('description', '').strip()
        guid = item.findtext('guid', '').strip()
        pub_date = item.findtext('pubDate', '').strip()

        # Filter: check title AND description for our stock
        # Multi-company reports have a generic title but list tickers in the body
        searchable = (title + ' ' + desc).lower()
        ticker_lower = ticker.lower()
        company_lower = company.lower()

        # Also match generic "company watch" reports that contain our ticker in body
        is_company_watch_report = 'company watch' in title.lower()

        if ticker_lower in searchable or company_lower in searchable or is_company_watch_report:
            items.append({
                'title': title,
                'link': link,
                'description': desc,
                'guid': guid,
                'pub_date': pub_date,
                '_ticker': ticker,  # tag with ticker for multi-stock
            })
            logger.info("Found relevant report: %s", title)

    logger.info("RSS scan (%s): %d relevant items from feed", ticker, len(items))
    return items


def parse_pub_date(date_str):
    """Parse RSS pubDate into ISO format."""
    if not date_str:
        return datetime.now(timezone.utc).isoformat()

    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue

    return datetime.now(timezone.utc).isoformat()


def parse_report_stance(text):
    """
    Extract stance (BUY/SELL/HOLD/FADE) and confidence from report text.
    Reports follow pattern like: "HOLD - 62" or title like "BABA - HOLD - 62"
    """
    stance = None
    confidence = None

    # Try title-style: "BABA - HOLD - 62"
    title_match = re.search(
        r'(?:BUY|SELL|HOLD|LONG|SHORT|WATCH|FADE)\s*[-:]\s*(\d+)',
        text, re.IGNORECASE
    )
    if title_match:
        confidence = float(title_match.group(1))

    # Extract stance keyword
    stance_match = re.search(
        r'\b(BUY|SELL|HOLD|LONG|SHORT|WATCH|FADE)\b',
        text, re.IGNORECASE
    )
    if stance_match:
        raw = stance_match.group(1).upper()
        # Normalize: LONG -> BUY, SHORT -> SELL, WATCH -> HOLD
        stance_map = {
            'BUY': 'BUY', 'LONG': 'BUY',
            'SELL': 'SELL', 'SHORT': 'SELL',
            'HOLD': 'HOLD', 'WATCH': 'HOLD',
            'FADE': 'FADE',
        }
        stance = stance_map.get(raw, 'HOLD')

    # Try confidence from text: "Confidence: 62%" or "62%"
    if confidence is None:
        conf_match = re.search(r'(?:confidence|conviction)[:\s]*(\d+)\s*%?', text, re.IGNORECASE)
        if conf_match:
            confidence = float(conf_match.group(1))

    if confidence is None:
        # Try "- NN" at end of title
        end_match = re.search(r'[-]\s*(\d{1,3})\s*$', text.strip())
        if end_match:
            confidence = float(end_match.group(1))

    return stance, confidence


def extract_ticker_section(text, ticker):
    """
    Extract the section for a specific ticker from a multi-company report.
    Reports have per-company sections like:
      '2026-02-16 - Alibaba Group Holding Limited - BABA - HOLD - 52%'
    followed by detailed analysis until the next company section.
    Returns the ticker-specific text block, or the full text if not found.
    """
    # Find the section header for this ticker
    # Pattern: "TICKER - STANCE - NN%"  or full line with date/company
    pattern = re.compile(
        r'^.*?\b' + re.escape(ticker) + r'\b.*?(?:BUY|SELL|HOLD|FADE)\s*[-:]\s*\d+',
        re.IGNORECASE | re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return text  # Fallback to full text

    start = match.start()

    # Find the next company section (another "DATE - Company - TICKER - STANCE - NN%")
    # or end of text
    next_section = re.search(
        r'\n\S.*?\b(?:NYSE|NASDAQ|HKEX)\b\)',
        text[match.end():]
    )
    if next_section:
        end = match.end() + next_section.start()
    else:
        end = len(text)

    return text[start:end]


def parse_ticker_stance_from_table(text, ticker):
    """
    Parse stance and confidence for a specific ticker from the executive dashboard table.
    Table rows look like:
      | Alibaba Group Holding Limited | BABA | HOLD | 52% | negative | ...
    Or in plain text after HTML stripping:
      Alibaba Group Holding Limited
      BABA
      HOLD
      52%
    Also matches: '2026-02-16 - Company Name - TICKER - HOLD - 52%'
    """
    # Try the per-company section header pattern first
    # "TICKER - HOLD - 52%" or "TICKER - HOLD - 52"
    header_match = re.search(
        r'\b' + re.escape(ticker) + r'\s*[-]\s*(BUY|SELL|HOLD|FADE)\s*[-]\s*(\d+)\s*%?',
        text, re.IGNORECASE
    )
    if header_match:
        stance = header_match.group(1).upper()
        confidence = float(header_match.group(2))
        return stance, confidence

    # Try markdown table: | TICKER | **HOLD** | 52% |
    table_match = re.search(
        r'\b' + re.escape(ticker) + r'\b.*?\*{0,2}(BUY|SELL|HOLD|FADE)\*{0,2}\s*\|?\s*(\d+)\s*%?',
        text, re.IGNORECASE
    )
    if table_match:
        stance = table_match.group(1).upper()
        confidence = float(table_match.group(2))
        return stance, confidence

    return None, None


def parse_report_sections(text):
    """
    Extract key sections from report text:
    - rationale, watchpoints, risks, mispricing
    - market probability distribution
    """
    sections = {
        'rationale': '',
        'watchpoints': '',
        'risks': '',
        'mispricing': '',
        'market_upside_pct': None,
        'market_sideways_pct': None,
        'market_downside_pct': None,
        'dominant_risk': '',
        'dominant_upside': '',
    }

    # Extract probability distribution
    upside_match = re.search(r'~?(\d+)%\s*(?:upside|bullish)', text, re.IGNORECASE)
    sideways_match = re.search(r'~?(\d+)%\s*(?:sideways|neutral|flat)', text, re.IGNORECASE)
    downside_match = re.search(r'~?(\d+)%\s*(?:downside|bearish)', text, re.IGNORECASE)

    if upside_match:
        sections['market_upside_pct'] = float(upside_match.group(1))
    if sideways_match:
        sections['market_sideways_pct'] = float(sideways_match.group(1))
    if downside_match:
        sections['market_downside_pct'] = float(downside_match.group(1))

    # Extract rationale - look for the main recommendation sentence
    # Usually starts with stance word: "HOLD: ..." or "BUY: ..."
    rationale_match = re.search(
        r'(?:BUY|SELL|HOLD|WATCH|FADE)\s*:\s*(.+?)(?:\n|$)',
        text, re.IGNORECASE
    )
    if rationale_match:
        sections['rationale'] = rationale_match.group(1).strip()

    # Extract watchpoints section
    watch_match = re.search(
        r'(?:watch\s*points?|near.term\s*watch|monitoring)[:\s]*\n?((?:[-\u2022*]\s*.+\n?)+)',
        text, re.IGNORECASE
    )
    if watch_match:
        sections['watchpoints'] = watch_match.group(1).strip()

    # Extract risk flags
    risk_match = re.search(
        r'(?:risk\s*flags?|key\s*risks?)[:\s]*\n?((?:[-\u2022*]\s*.+\n?)+)',
        text, re.IGNORECASE
    )
    if risk_match:
        sections['risks'] = risk_match.group(1).strip()

    # Extract mispricing section
    misprice_match = re.search(
        r'(?:mispriced|mispricing|what may be mispriced)[:\s]*\n?((?:[-\u2022*\d]\s*.+\n?)+)',
        text, re.IGNORECASE
    )
    if misprice_match:
        sections['mispricing'] = misprice_match.group(1).strip()

    # Extract dominant risk/upside
    dom_risk_match = re.search(r'(?:dominant\s*risk|primary\s*risk)[:\s]*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if dom_risk_match:
        sections['dominant_risk'] = dom_risk_match.group(1).strip()

    dom_up_match = re.search(r'(?:dominant\s*upside|primary\s*upside)[:\s]*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if dom_up_match:
        sections['dominant_upside'] = dom_up_match.group(1).strip()

    return sections


def fetch_report_content(url):
    """Fetch the full report page and extract text content."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        html = resp.text
        text = html_to_text(html)
        return html, text
    except Exception as e:
        logger.error("Failed to fetch report %s: %s", url, e)
        return None, None


def ingest_report(item, ticker=None):
    """
    Ingest a single RSS item into the database.
    Returns True if new report was ingested.
    """
    ticker = ticker or item.get('_ticker') or WATCHED_TICKER
    conn = get_db()

    # For multi-company reports, make guid unique per ticker
    # so the same report is ingested once per watched stock
    report_guid = item['guid'] + '::' + ticker if ticker else item['guid']

    # Check if already ingested
    existing = conn.execute(
        "SELECT id FROM reports WHERE rss_guid=?", (report_guid,)
    ).fetchone()
    if existing:
        conn.close()
        return False

    pub_date = parse_pub_date(item['pub_date'])

    # Fetch full report content
    report_html, report_text = fetch_report_content(item['link'])

    # Also try parsing from the RSS description (often has the full content)
    desc_text = html_to_text(item['description']) if item['description'] else ''

    # Combine title + description + full page for parsing
    combined_text = item['title'] + '\n' + desc_text
    if report_text:
        combined_text += '\n' + report_text

    # For multi-company reports: extract this ticker's specific stance/confidence
    # from the executive dashboard table or section headers
    stance, confidence = parse_ticker_stance_from_table(combined_text, ticker)

    # Fallback: try generic parsing from title
    if stance is None:
        stance, confidence = parse_report_stance(item['title'])

    # If still nothing, try from full content
    if stance is None:
        stance, confidence = parse_report_stance(combined_text)

    # Extract this ticker's specific section for detailed parsing
    ticker_text = extract_ticker_section(combined_text, ticker)

    # Parse sections from the ticker-specific text (not the whole report)
    sections = parse_report_sections(ticker_text)

    # If rationale is empty, use the description as rationale
    if not sections['rationale'] and desc_text:
        # Take first meaningful sentence
        sentences = [s.strip() for s in desc_text.split('.') if len(s.strip()) > 20]
        if sentences:
            sections['rationale'] = sentences[0] + '.'

    conn.execute("""
        INSERT INTO reports (
            ticker, report_url, title, published_date, rss_guid,
            report_stance, report_confidence, report_rationale,
            report_watchpoints, report_risks, report_mispricing,
            market_upside_pct, market_sideways_pct, market_downside_pct,
            dominant_risk, dominant_upside,
            report_html, report_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker,
        item['link'],
        item['title'],
        pub_date,
        report_guid,
        stance,
        confidence,
        sections['rationale'],
        sections['watchpoints'],
        sections['risks'],
        sections['mispricing'],
        sections['market_upside_pct'],
        sections['market_sideways_pct'],
        sections['market_downside_pct'],
        sections['dominant_risk'],
        sections['dominant_upside'],
        report_html,
        report_text,
    ))
    conn.commit()
    conn.close()

    logger.info("Ingested report: %s (stance=%s, confidence=%s)", item['title'], stance, confidence)
    return True


def scan(ticker=None, company=None, rss_url=None):
    """Main scan function: fetch RSS, ingest new reports.
    If no params given, scans all stocks in WATCHED_STOCKS.
    """
    if ticker:
        # Single stock scan
        items = fetch_rss(rss_url=rss_url, ticker=ticker, company=company)
        if not items:
            logger.info("No new reports found for %s", ticker)
            return 0
        ingested = 0
        for item in items:
            if ingest_report(item, ticker=ticker):
                ingested += 1
        if ingested:
            logger.info("Ingested %d new report(s) for %s", ingested, ticker)
        return ingested

    # Multi-stock: scan all watched stocks
    total_ingested = 0
    for stock in WATCHED_STOCKS:
        items = fetch_rss(
            rss_url=stock['rss_url'],
            ticker=stock['ticker'],
            company=stock['company'],
        )
        for item in items:
            if ingest_report(item, ticker=stock['ticker']):
                total_ingested += 1

    if total_ingested:
        logger.info("Ingested %d new report(s) across all stocks", total_ingested)
    return total_ingested
