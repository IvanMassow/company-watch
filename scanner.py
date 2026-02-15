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

from config import RSS_URL, WATCHED_TICKER, WATCHED_COMPANY
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


def fetch_rss():
    """Fetch RSS feed and return items relevant to our watched stock."""
    try:
        resp = requests.get(RSS_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("RSS fetch failed: %s", e)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error("RSS parse failed: %s", e)
        return []

    items = []
    for item in root.findall('.//item'):
        title = item.findtext('title', '').strip()
        link = item.findtext('link', '').strip()
        desc = item.findtext('description', '').strip()
        guid = item.findtext('guid', '').strip()
        pub_date = item.findtext('pubDate', '').strip()

        # Filter: only process reports mentioning our stock
        title_lower = title.lower()
        ticker_lower = WATCHED_TICKER.lower()
        company_lower = WATCHED_COMPANY.lower()

        if ticker_lower in title_lower or company_lower in title_lower:
            items.append({
                'title': title,
                'link': link,
                'description': desc,
                'guid': guid,
                'pub_date': pub_date,
            })
            logger.info("Found relevant report: %s", title)

    logger.info("RSS scan: %d relevant items from feed", len(items))
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


def ingest_report(item):
    """
    Ingest a single RSS item into the database.
    Returns True if new report was ingested.
    """
    conn = get_db()

    # Check if already ingested
    existing = conn.execute(
        "SELECT id FROM reports WHERE rss_guid=?", (item['guid'],)
    ).fetchone()
    if existing:
        conn.close()
        return False

    # Parse stance from title
    stance, confidence = parse_report_stance(item['title'])
    pub_date = parse_pub_date(item['pub_date'])

    # Fetch full report content
    report_html, report_text = fetch_report_content(item['link'])

    # Also try parsing from the RSS description (often has the full content)
    desc_text = html_to_text(item['description']) if item['description'] else ''

    # Combine title + description + full page for parsing
    combined_text = item['title'] + '\n' + desc_text
    if report_text:
        combined_text += '\n' + report_text

    # If we didn't get stance from title, try from content
    if stance is None:
        stance, confidence = parse_report_stance(combined_text)

    # Parse sections from content
    sections = parse_report_sections(combined_text)

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
        WATCHED_TICKER,
        item['link'],
        item['title'],
        pub_date,
        item['guid'],
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


def scan():
    """Main scan function: fetch RSS, ingest new reports."""
    items = fetch_rss()
    if not items:
        logger.info("No new reports found")
        return 0

    ingested = 0
    for item in items:
        if ingest_report(item):
            ingested += 1

    if ingested:
        logger.info("Ingested %d new report(s)", ingested)
    return ingested
