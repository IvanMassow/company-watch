"""
Company Watch - Market Intelligence Module
Real-time market data for the trading bot's due diligence:
  1. NEWS_SENTIMENT: AI-scored news sentiment per ticker (Alpha Vantage)
  2. REALTIME_BULK_QUOTES: Extended-hours / pre-market prices (Alpha Vantage)
  3. HK exchange data: Dual-listed tickers (e.g. BABA -> 9988.HK)
  4. Market overview: S&P 500 and macro context

This gives the LLM real data to work with instead of hallucinating.
The user pays ~$100/month for Alpha Vantage — use it properly.
"""
import logging
import time
from datetime import datetime, timezone

import requests

from config import ALPHA_VANTAGE_KEY, ALPHA_VANTAGE_BASE, AV_RATE_LIMIT

logger = logging.getLogger("companywatch.intel")

# Dual-listed tickers: NYSE -> overseas exchange symbol
# BABA trades as 9988.HK in Hong Kong
# JD trades as 9618.HK
# BIDU trades as 9888.HK
# NTES trades as 9999.HK
# PDD trades as N/A (US-only via Cayman)
DUAL_LISTED = {
    'BABA': '9988.HK',
    'JD':   '9618.HK',
    'BIDU': '9888.HK',
    'NTES': '9999.HK',
}

# Cache to avoid redundant API calls within the same cycle
_intel_cache = {}
_CACHE_TTL = 300  # 5 minute cache


def _cache_get(key):
    """Get from cache if still fresh."""
    if key in _intel_cache:
        ts, data = _intel_cache[key]
        if (datetime.now(timezone.utc) - ts).total_seconds() < _CACHE_TTL:
            return data
    return None


def _cache_set(key, data):
    """Store in cache."""
    _intel_cache[key] = (datetime.now(timezone.utc), data)


def fetch_news_sentiment(ticker, limit=10):
    """
    Fetch AI-scored news sentiment from Alpha Vantage NEWS_SENTIMENT.
    Returns a list of news items with sentiment scores, or empty list.

    Each item has:
      - title, url, source, published
      - overall_sentiment_score (-1 to 1)
      - overall_sentiment_label (Bearish/Somewhat-Bearish/Neutral/Somewhat-Bullish/Bullish)
      - ticker_sentiment_score (specific to our ticker)
      - ticker_relevance_score (0-1, how relevant to our ticker)
      - summary (brief text)
    """
    cache_key = 'news_' + ticker
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not ALPHA_VANTAGE_KEY:
        return []

    try:
        time.sleep(AV_RATE_LIMIT)
        resp = requests.get(ALPHA_VANTAGE_BASE, params={
            'function': 'NEWS_SENTIMENT',
            'tickers': ticker,
            'limit': limit,
            'apikey': ALPHA_VANTAGE_KEY,
        }, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if 'Note' in data or 'Information' in data:
            logger.warning("AV rate limit on NEWS_SENTIMENT for %s", ticker)
            return []

        feed = data.get('feed', [])
        results = []

        for article in feed[:limit]:
            # Find ticker-specific sentiment
            ticker_sentiment = None
            ticker_relevance = 0
            for ts in article.get('ticker_sentiment', []):
                if ts.get('ticker', '').upper() == ticker.upper():
                    ticker_sentiment = float(ts.get('ticker_sentiment_score', 0))
                    ticker_relevance = float(ts.get('relevance_score', 0))
                    break

            results.append({
                'title': article.get('title', ''),
                'url': article.get('url', ''),
                'source': article.get('source', ''),
                'published': article.get('time_published', ''),
                'summary': article.get('summary', ''),
                'overall_sentiment_score': float(article.get('overall_sentiment_score', 0)),
                'overall_sentiment_label': article.get('overall_sentiment_label', 'Neutral'),
                'ticker_sentiment_score': ticker_sentiment,
                'ticker_relevance_score': ticker_relevance,
            })

        _cache_set(cache_key, results)
        logger.info("News sentiment for %s: %d articles fetched", ticker, len(results))
        return results

    except Exception as e:
        logger.error("NEWS_SENTIMENT failed for %s: %s", ticker, e)
        return []


def fetch_extended_hours_quote(ticker):
    """
    Fetch extended-hours (pre-market / after-hours) quote from Alpha Vantage.
    Uses GLOBAL_QUOTE which on the paid tier includes extended hours data.

    Returns dict with:
      - price, open, high, low, volume, change_pct (regular session)
      - extended_price, extended_change, extended_change_pct (if available)
    Or None on failure.
    """
    cache_key = 'ext_' + ticker
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not ALPHA_VANTAGE_KEY:
        return None

    try:
        time.sleep(AV_RATE_LIMIT)
        resp = requests.get(ALPHA_VANTAGE_BASE, params={
            'function': 'GLOBAL_QUOTE',
            'symbol': ticker,
            'apikey': ALPHA_VANTAGE_KEY,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if 'Note' in data or 'Information' in data:
            logger.warning("AV rate limit on extended quote for %s", ticker)
            return None

        quote = data.get('Global Quote', {})
        if not quote or '05. price' not in quote:
            return None

        result = {
            'price': float(quote.get('05. price', 0)),
            'open': float(quote.get('02. open', 0)),
            'high': float(quote.get('03. high', 0)),
            'low': float(quote.get('04. low', 0)),
            'volume': float(quote.get('06. volume', 0)),
            'change_pct': float(quote.get('10. change percent', '0').rstrip('%')),
            'previous_close': float(quote.get('08. previous close', 0)),
        }

        _cache_set(cache_key, result)
        return result

    except Exception as e:
        logger.error("Extended quote failed for %s: %s", ticker, e)
        return None


def fetch_hk_quote(ticker):
    """
    Fetch the Hong Kong exchange quote for a dual-listed stock.
    Uses the DUAL_LISTED mapping (e.g. BABA -> 9988.HK).

    Returns dict with price data or None if not dual-listed or on failure.
    """
    hk_symbol = DUAL_LISTED.get(ticker)
    if not hk_symbol:
        return None

    cache_key = 'hk_' + hk_symbol
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not ALPHA_VANTAGE_KEY:
        return None

    try:
        time.sleep(AV_RATE_LIMIT)
        resp = requests.get(ALPHA_VANTAGE_BASE, params={
            'function': 'GLOBAL_QUOTE',
            'symbol': hk_symbol,
            'apikey': ALPHA_VANTAGE_KEY,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if 'Note' in data or 'Information' in data:
            logger.warning("AV rate limit on HK quote for %s", hk_symbol)
            return None

        quote = data.get('Global Quote', {})
        if not quote or '05. price' not in quote:
            logger.info("No HK quote data for %s (market may be closed)", hk_symbol)
            return None

        result = {
            'symbol': hk_symbol,
            'price_hkd': float(quote.get('05. price', 0)),
            'change_pct': float(quote.get('10. change percent', '0').rstrip('%')),
            'volume': float(quote.get('06. volume', 0)),
            'previous_close': float(quote.get('08. previous close', 0)),
        }

        _cache_set(cache_key, result)
        logger.info("HK quote for %s (%s): HKD %.2f (%.2f%%)",
                     ticker, hk_symbol, result['price_hkd'], result['change_pct'])
        return result

    except Exception as e:
        logger.error("HK quote failed for %s (%s): %s", ticker, hk_symbol, e)
        return None


def fetch_market_overview():
    """
    Fetch broad market context: S&P 500 (SPY), VIX, and US 10Y yield.
    Returns dict with market data.
    """
    cache_key = 'market_overview'
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    result = {}

    # SPY (S&P 500 ETF)
    try:
        time.sleep(AV_RATE_LIMIT)
        resp = requests.get(ALPHA_VANTAGE_BASE, params={
            'function': 'GLOBAL_QUOTE',
            'symbol': 'SPY',
            'apikey': ALPHA_VANTAGE_KEY,
        }, timeout=15)
        data = resp.json()
        quote = data.get('Global Quote', {})
        if quote and '05. price' in quote:
            result['spy_price'] = float(quote['05. price'])
            result['spy_change_pct'] = float(quote.get('10. change percent', '0').rstrip('%'))
    except Exception as e:
        logger.error("SPY quote failed: %s", e)

    # VIX (volatility index) — use CBOE VIX
    try:
        time.sleep(AV_RATE_LIMIT)
        resp = requests.get(ALPHA_VANTAGE_BASE, params={
            'function': 'GLOBAL_QUOTE',
            'symbol': 'VIX',
            'apikey': ALPHA_VANTAGE_KEY,
        }, timeout=15)
        data = resp.json()
        quote = data.get('Global Quote', {})
        if quote and '05. price' in quote:
            result['vix'] = float(quote['05. price'])
            result['vix_change_pct'] = float(quote.get('10. change percent', '0').rstrip('%'))
    except Exception as e:
        logger.debug("VIX quote failed (may not be available): %s", e)

    _cache_set(cache_key, result)
    return result


def gather_full_intel(ticker):
    """
    Gather ALL available intelligence for a ticker.
    This is the main entry point — calls all data sources and packages
    the result into a structured briefing for the LLM.

    Returns dict:
    {
        'ticker': 'BABA',
        'timestamp': '2026-02-16T14:00:00+00:00',
        'news': [...],             # AI-scored news articles
        'news_summary': str,       # Pre-formatted news digest
        'hk_quote': {...},         # Hong Kong exchange data (if dual-listed)
        'market': {...},           # SPY, VIX, macro context
        'extended_quote': {...},   # Pre-market / after-hours quote
    }
    """
    logger.info("Gathering full market intelligence for %s", ticker)

    intel = {
        'ticker': ticker,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'news': [],
        'news_summary': '',
        'hk_quote': None,
        'market': {},
        'extended_quote': None,
    }

    # 1. News sentiment
    news = fetch_news_sentiment(ticker, limit=10)
    intel['news'] = news
    if news:
        intel['news_summary'] = format_news_digest(news, ticker)

    # 2. HK exchange quote (for dual-listed)
    hk = fetch_hk_quote(ticker)
    intel['hk_quote'] = hk

    # 3. Market overview
    intel['market'] = fetch_market_overview()

    # 4. Extended hours quote
    ext = fetch_extended_hours_quote(ticker)
    intel['extended_quote'] = ext

    logger.info("Intel gathered for %s: %d news, HK=%s, market=%s",
                ticker, len(news),
                'yes' if hk else 'no',
                'yes' if intel['market'] else 'no')

    return intel


def format_news_digest(news_items, ticker):
    """
    Format news items into a concise digest string for the LLM.
    Focuses on: headline, source, sentiment, and relevance.
    """
    if not news_items:
        return "No recent news found."

    lines = []
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0

    for i, item in enumerate(news_items[:10], 1):
        sentiment = item.get('overall_sentiment_label', 'Neutral')
        score = item.get('ticker_sentiment_score')
        relevance = item.get('ticker_relevance_score', 0)

        if score is not None:
            if score > 0.15:
                bullish_count += 1
            elif score < -0.15:
                bearish_count += 1
            else:
                neutral_count += 1

        score_str = '{:+.3f}'.format(score) if score is not None else 'N/A'
        rel_str = '{:.0f}%'.format(relevance * 100) if relevance else ''

        lines.append(
            '{i}. [{sentiment}] {title}\n'
            '   Source: {source} | Ticker sentiment: {score} | Relevance: {rel}\n'
            '   {summary}'.format(
                i=i,
                sentiment=sentiment,
                title=item.get('title', 'No title'),
                source=item.get('source', 'Unknown'),
                score=score_str,
                rel=rel_str,
                summary=(item.get('summary', '')[:200] + '...' if len(item.get('summary', '')) > 200
                         else item.get('summary', '')),
            )
        )

    header = 'NEWS WIRE DIGEST ({n} articles: {b} bullish, {bear} bearish, {neut} neutral)'.format(
        n=len(news_items),
        b=bullish_count,
        bear=bearish_count,
        neut=neutral_count,
    )

    return header + '\n' + '\n'.join(lines)


def format_intel_briefing(intel, price_data=None):
    """
    Format the full intel package into a text briefing for the LLM.
    This is what gets injected into the LLM prompt.
    """
    sections = []

    # Market overview
    market = intel.get('market', {})
    if market:
        spy_line = ''
        if 'spy_price' in market:
            spy_line = 'S&P 500 (SPY): ${:.2f} ({:+.2f}%)'.format(
                market['spy_price'], market.get('spy_change_pct', 0))
        vix_line = ''
        if 'vix' in market:
            vix_line = 'VIX: {:.1f} ({:+.2f}%)'.format(
                market['vix'], market.get('vix_change_pct', 0))
        if spy_line or vix_line:
            sections.append('MARKET OVERVIEW:\n' + '\n'.join(filter(None, [spy_line, vix_line])))

    # Extended hours
    ext = intel.get('extended_quote')
    if ext and price_data:
        sections.append(
            'EXTENDED HOURS:\n'
            'Last regular price: ${:.2f} | Day change: {:+.2f}%\n'
            'Previous close: ${:.2f}'.format(
                ext.get('price', 0),
                ext.get('change_pct', 0),
                ext.get('previous_close', 0),
            )
        )

    # HK exchange
    hk = intel.get('hk_quote')
    if hk:
        sections.append(
            'HONG KONG EXCHANGE ({symbol}):\n'
            'HK price: HKD {price:.2f} ({change:+.2f}%)\n'
            'Volume: {vol:,.0f} | Previous close: HKD {prev:.2f}\n'
            'NOTE: HK session may have already reacted to overnight news.'.format(
                symbol=hk['symbol'],
                price=hk['price_hkd'],
                change=hk['change_pct'],
                vol=hk.get('volume', 0),
                prev=hk.get('previous_close', 0),
            )
        )

    # News digest
    news_summary = intel.get('news_summary', '')
    if news_summary:
        sections.append(news_summary)

    if not sections:
        return 'No additional market intelligence available at this time.'

    return '\n\n'.join(sections)
