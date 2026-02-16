"""
Company Watch - LLM Due Diligence Engine
Uses OpenAI GPT-4o-mini as a hedge fund portfolio manager who:
  1. Receives phenomenal intelligence reports (Company Watch signal analysis)
  2. Cross-references against real market data: news wires, HK tape, pre-market
  3. Makes independent trading decisions — can agree, amplify, or override the report
  4. Runs autonomous checks between reports — watches the tape, reads the wires
  5. Acts like a professional trader with skin in the game

The intelligence report is the crown jewel: it reads the entire world's news,
distils it through vertical analysis, and produces a single directional signal.
But even the best signal can be overtaken by events. This bot bridges that gap.
"""
import json
import logging

import requests

from config import OPENAI_API_KEY
from market_intel import gather_full_intel, format_intel_briefing

logger = logging.getLogger("companywatch.llm")

# ================================================================
# THE HEDGE FUND PORTFOLIO MANAGER PERSONA
# ================================================================
SYSTEM_PROMPT = """You are a senior portfolio manager at a systematic macro hedge fund.
You manage a concentrated book of Chinese ADR positions (BABA, JD, BIDU, NTES, PDD).

YOUR EDGE:
You receive a daily intelligence report from "Company Watch" — a signal analysis system
that reads EVERY piece of news published worldwide in the last 12 hours, filters it through
sector-specific verticals, and distils it into a single directional call with confidence.
This is not a Bloomberg summary. This is not a chatbot opinion. This report represents
thousands of articles processed through narrative signal analysis, producing a result
that mainstream analysts won't see for hours or days. Treat it with the respect it deserves.

However, you are NOT a passive consumer of this intelligence:

1. THE REPORT IS YOUR STARTING POINT, NOT YOUR CONCLUSION.
   You read it carefully. You understand its thesis. You respect the work behind it.
   But you also check the tape, read the news wires, and look at what's happening
   in Hong Kong and the broader market. If something has changed since the report
   was filed, you act on it. If the report missed something obvious in price action,
   you catch it.

2. YOU HAVE REAL MARKET DATA.
   You are provided with live news sentiment (AI-scored), Hong Kong exchange quotes
   for dual-listed stocks, S&P 500 levels, and VIX readings. USE THIS DATA.
   Don't guess what HK is doing — you can SEE it. Don't wonder about overnight news —
   you have the wire. This data is expensive. Use every piece of it.

3. YOU THINK LIKE A TRADER, NOT AN ANALYST.
   An analyst writes a report. A trader makes a decision. You care about:
   - Has the horse already bolted? (Price moved before we could act)
   - Is the market crashing? (Macro overwhelms micro thesis)
   - Should we take profits? (Good trade ≠ hold forever)
   - Is this the kind of move that reverses in 60 minutes? (Duck and cover)
   - What's the HK tape telling us? (Overnight reaction)

4. YOUR CONFIDENCE IS INDEPENDENT.
   The report gives its confidence (e.g., HOLD 52%). You give YOUR confidence.
   If you think the report is right but understated, your house confidence could be 75%.
   If you think the report missed something worrying, your confidence could be 30%.
   If you think the report is flat wrong, you override it entirely.

5. YOU HAVE SKIN IN THE GAME.
   Every decision you make affects P&L. Be decisive but not reckless.
   HOLD is not failure — it means "I don't have edge here, wait for clarity."
   But don't hold out of laziness. If the thesis is broken, get out.

STANCE OPTIONS:
- BUY: Enter long position (or increase conviction on existing long)
- SELL: Exit long / enter short
- HOLD: Maintain current position, thesis still valid
- FADE: Reduce exposure, thesis weakening but not dead

IMPORTANT RULES:
- Do NOT kill a signal just because Bloomberg hasn't picked it up yet. That's the POINT.
- Low confidence (45-55%) means the report is being honest about uncertainty — respect that.
- Check if HK has already moved. If BABA's Hong Kong listing (9988.HK) is down 4%,
  the NYSE is likely to follow. Factor this in.
- If the news wire is full of bearish articles, but the report says BUY, dig into WHY.
  Maybe the report saw something the wire hasn't caught yet.
- Always explain your reasoning. Be specific. Cite the data you used."""


def _call_llm(system_prompt, user_prompt, max_tokens=2048):
    """Make an OpenAI API call and return parsed JSON response."""
    if not OPENAI_API_KEY:
        logger.warning("No OPENAI_API_KEY set - skipping LLM DD")
        return None

    try:
        resp = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={
                'Authorization': 'Bearer ' + OPENAI_API_KEY,
                'Content-Type': 'application/json',
            },
            json={
                'model': 'gpt-4o-mini',
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                'max_tokens': max_tokens,
                'temperature': 0.3,
                'response_format': {'type': 'json_object'},
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content']
        return json.loads(content)
    except json.JSONDecodeError:
        logger.error("LLM returned non-JSON response")
        return None
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return None


def assess_report(report, price_data, current_position, ticker=None):
    """
    Assess a new daily report and decide whether to follow, modify, or override it.
    Now includes real market intelligence: news wire, HK tape, macro context.

    Returns:
    {
        "decision": "BUY" | "SELL" | "HOLD" | "FADE",
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "house_confidence_pct": 0-100,
        "reason": "explanation",
        "agrees_with_report": true/false,
        "override_reason": "why overriding (if applicable)"
    }
    """
    ticker = ticker or 'BABA'

    pos_state = 'FLAT'
    pos_direction = None
    pos_entry = None
    pos_pnl = None

    if current_position and current_position.get('state') != 'FLAT':
        pos_state = current_position['state']
        pos_direction = current_position.get('direction')
        pos_entry = current_position.get('entry_price')
        if pos_entry and price_data:
            from tracker import calculate_pnl
            pos_pnl = calculate_pnl(pos_entry, price_data['price'], pos_direction or 'LONG')

    # === GATHER REAL MARKET INTELLIGENCE ===
    intel = gather_full_intel(ticker)
    intel_briefing = format_intel_briefing(intel, price_data)

    prompt = """DAILY REPORT ASSESSMENT — {ticker}

═══════════════════════════════════════════════════════
INTELLIGENCE REPORT (Company Watch Signal Analysis)
═══════════════════════════════════════════════════════
This report processed thousands of global news articles through narrative signal
analysis. It represents hours of AI-driven intelligence work. Read it carefully.

Report Stance: {stance}
Report Confidence: {confidence}%
Report Rationale: {rationale}
Report Watchpoints: {watchpoints}
Report Risks: {risks}
Report Mispricing Notes: {mispricing}

═══════════════════════════════════════════════════════
YOUR BOOK — CURRENT POSITION
═══════════════════════════════════════════════════════
State: {pos_state}
Direction: {pos_direction}
Entry Price: {pos_entry}
Current P&L: {pos_pnl}

═══════════════════════════════════════════════════════
LIVE MARKET DATA (real-time, not simulated)
═══════════════════════════════════════════════════════
{ticker} Price: ${price:.2f}
Day Change: {change:+.2f}%
Volume: {volume:,.0f}

═══════════════════════════════════════════════════════
MARKET INTELLIGENCE WIRE
═══════════════════════════════════════════════════════
{intel_briefing}

═══════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════
1. Read the intelligence report — understand the thesis and WHY it reached this conclusion.
2. Cross-reference against the news wire — does the sentiment data support or contradict?
3. Check the HK tape — has the overnight session already priced in this move?
4. Check the macro — is S&P/VIX suggesting risk-off or risk-on?
5. Make YOUR call — agree, amplify, reduce, or override the report.

You must provide your OWN house confidence percentage (0-100).
This can be HIGHER than the report (your research confirms it, you're more confident)
or LOWER (something worries you that the report missed).

For example:
- Report says HOLD 52%, you see HK is flat and news is neutral → house HOLD 55%
- Report says BUY 70%, but HK is down 3% and Pentagon blacklist news → house HOLD 40%
- Report says HOLD 52%, but news wire is very bullish and HK is up 2% → house BUY 72%

Respond in JSON:
{{
    "decision": "BUY|SELL|HOLD|FADE",
    "confidence": "HIGH|MEDIUM|LOW",
    "house_confidence_pct": 0-100,
    "reason": "2-3 sentence explanation citing specific data points",
    "agrees_with_report": true/false,
    "override_reason": "if disagreeing, why — cite the evidence"
}}""".format(
        ticker=ticker,
        stance=report.get('report_stance', 'UNKNOWN'),
        confidence=report.get('report_confidence', 0),
        rationale=report.get('report_rationale', 'N/A'),
        watchpoints=report.get('report_watchpoints', 'N/A'),
        risks=report.get('report_risks', 'N/A'),
        mispricing=report.get('report_mispricing', 'N/A'),
        pos_state=pos_state,
        pos_direction=pos_direction or 'N/A',
        pos_entry='${:.2f}'.format(pos_entry) if pos_entry else 'N/A',
        pos_pnl='{:+.2f}%'.format(pos_pnl) if pos_pnl is not None else 'N/A',
        price=price_data.get('price', 0) if price_data else 0,
        change=price_data.get('change_pct', 0) if price_data else 0,
        volume=price_data.get('volume', 0) if price_data else 0,
        intel_briefing=intel_briefing,
    )

    return _call_llm(SYSTEM_PROMPT, prompt)


def autonomous_check(current_position, price_data, latest_report, ticker=None):
    """
    Autonomous check between reports — watching the tape and the wires.
    This is your intraday surveillance: something may have changed since the morning report.

    Returns:
    {
        "action": "HOLD" | "EXIT" | "REVERSE",
        "reason": "explanation",
        "urgency": "HIGH" | "MEDIUM" | "LOW"
    }
    """
    if not current_position or current_position.get('state') == 'FLAT':
        return None

    ticker = ticker or 'BABA'
    direction = current_position.get('direction', 'LONG')
    entry_price = current_position.get('entry_price', 0)
    peak_price = current_position.get('peak_price', 0)
    price = price_data.get('price', 0) if price_data else 0

    from tracker import calculate_pnl
    current_pnl = calculate_pnl(entry_price, price, direction)
    peak_pnl = calculate_pnl(entry_price, peak_price, direction) if peak_price else 0

    report_age = 'unknown'
    if latest_report and latest_report.get('published_date'):
        try:
            from datetime import datetime, timezone
            pub = datetime.fromisoformat(latest_report['published_date'].replace('Z', '+00:00'))
            age_hours = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
            report_age = '{:.1f} hours ago'.format(age_hours)
        except Exception:
            pass

    # === GATHER FRESH MARKET INTELLIGENCE ===
    intel = gather_full_intel(ticker)
    intel_briefing = format_intel_briefing(intel, price_data)

    prompt = """INTRADAY SURVEILLANCE — {ticker}

You are checking your book between reports. The morning intelligence has already
been processed, but markets don't sleep and neither do you.

═══════════════════════════════════════════════════════
YOUR POSITION
═══════════════════════════════════════════════════════
Direction: {direction}
Entry: ${entry:.2f}
Current: ${price:.2f}
P&L: {pnl:+.2f}%
Peak P&L: {peak_pnl:+.2f}%
Peak Price: ${peak:.2f}

═══════════════════════════════════════════════════════
LAST INTELLIGENCE REPORT (filed {report_age})
═══════════════════════════════════════════════════════
Stance: {report_stance}
Confidence: {report_conf}%
Rationale: {report_rationale}

═══════════════════════════════════════════════════════
CURRENT TAPE
═══════════════════════════════════════════════════════
Day Change: {change:+.2f}%

═══════════════════════════════════════════════════════
LIVE INTELLIGENCE WIRE (since report was filed)
═══════════════════════════════════════════════════════
{intel_briefing}

═══════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════
1. Has anything changed since the morning report that it couldn't have known?
2. Is the news wire showing a shift in sentiment?
3. Should we protect profits after a strong run?
4. Is the loss getting too deep — is the thesis still intact?
5. Any sign of a market-wide event (SPY crash, VIX spike) that changes everything?

If everything looks fine, say HOLD. Only recommend EXIT or REVERSE if genuinely warranted.
A good trader doesn't churn — but a good trader doesn't sit on a broken thesis either.

Respond in JSON:
{{
    "action": "HOLD|EXIT|REVERSE",
    "reason": "1-2 sentence explanation — cite specific data",
    "urgency": "HIGH|MEDIUM|LOW"
}}""".format(
        ticker=ticker,
        direction=direction,
        entry=entry_price,
        price=price,
        pnl=current_pnl,
        peak_pnl=peak_pnl,
        peak=peak_price or price,
        report_stance=latest_report.get('report_stance', 'N/A') if latest_report else 'N/A',
        report_conf=latest_report.get('report_confidence', 0) if latest_report else 0,
        report_age=report_age,
        report_rationale=latest_report.get('report_rationale', 'N/A') if latest_report else 'N/A',
        change=price_data.get('change_pct', 0) if price_data else 0,
        intel_briefing=intel_briefing,
    )

    return _call_llm(SYSTEM_PROMPT, prompt, max_tokens=512)


def assess_loss(current_position, price_data, latest_report, ticker=None):
    """
    Called when position is at a soft stop-loss level.
    Decides whether to cut or hold through the pain — with real news context.

    Returns:
    {
        "action": "HOLD" | "EXIT",
        "reason": "explanation"
    }
    """
    ticker = ticker or 'BABA'
    direction = current_position.get('direction', 'LONG')
    entry_price = current_position.get('entry_price', 0)
    price = price_data.get('price', 0) if price_data else 0

    from tracker import calculate_pnl
    current_pnl = calculate_pnl(entry_price, price, direction)

    # Get fresh news to inform the decision
    intel = gather_full_intel(ticker)
    intel_briefing = format_intel_briefing(intel, price_data)

    prompt = """STOP-LOSS ASSESSMENT — {ticker}

Your position is in the red. Time to decide: cut and live to fight another day,
or hold because the thesis is fundamentally sound and this is temporary pain?

═══════════════════════════════════════════════════════
THE DAMAGE
═══════════════════════════════════════════════════════
Position: {direction} from ${entry:.2f}
Current: ${price:.2f}
P&L: {pnl:+.2f}%

═══════════════════════════════════════════════════════
INTELLIGENCE REPORT SAYS
═══════════════════════════════════════════════════════
Stance: {stance} | Confidence: {conf}%
Rationale: {rationale}

═══════════════════════════════════════════════════════
NEWS WIRE
═══════════════════════════════════════════════════════
{intel_briefing}

═══════════════════════════════════════════════════════
THE QUESTION
═══════════════════════════════════════════════════════
Is the thesis BROKEN or is this just volatility?
- If the news is driving this move and the fundamentals haven't changed → HOLD
- If the thesis itself is invalidated (regulatory, structural, macro) → EXIT
- If the report is still bullish but the tape says otherwise → weigh the evidence

A position with a valid thesis can recover. A broken thesis won't.
Be honest. Don't hold out of hope. Don't cut out of fear.

Respond in JSON:
{{
    "action": "HOLD|EXIT",
    "reason": "1-2 sentence explanation — what does the news wire tell you?"
}}""".format(
        ticker=ticker,
        direction=direction,
        entry=entry_price,
        price=price,
        pnl=current_pnl,
        stance=latest_report.get('report_stance', 'N/A') if latest_report else 'N/A',
        conf=latest_report.get('report_confidence', 0) if latest_report else 0,
        rationale=latest_report.get('report_rationale', 'N/A') if latest_report else 'N/A',
        intel_briefing=intel_briefing,
    )

    return _call_llm(SYSTEM_PROMPT, prompt, max_tokens=512)


def premarket_check(current_position, price_data, latest_report, ticker=None):
    """
    Pre-market DD: runs BEFORE NYSE opens.
    This is your MOST IMPORTANT job of the day. You have real data now:
    - HK tape (has the overnight session already reacted?)
    - News wire (what happened while NYSE was closed?)
    - Macro context (SPY futures, VIX)

    Returns:
    {
        "action": "HOLD" | "EXIT" | "DUCK",
        "urgency": "HIGH" | "MEDIUM" | "LOW",
        "duck_and_cover": true/false,
        "reason": "explanation"
    }
    """
    ticker = ticker or 'BABA'
    pos_state = 'FLAT'
    pos_direction = None
    pos_pnl = None

    if current_position and current_position.get('state') != 'FLAT':
        pos_state = current_position['state']
        pos_direction = current_position.get('direction')
        entry = current_position.get('entry_price', 0)
        if entry and price_data:
            from tracker import calculate_pnl
            pos_pnl = calculate_pnl(entry, price_data.get('price', 0), pos_direction or 'LONG')

    # === GATHER PRE-MARKET INTELLIGENCE ===
    # This is where the real data matters most
    intel = gather_full_intel(ticker)
    intel_briefing = format_intel_briefing(intel, price_data)

    prompt = """PRE-MARKET BRIEFING — {ticker}

═══════════════════════════════════════════════════════
SITUATION
═══════════════════════════════════════════════════════
NYSE opens soon. You need to decide what happens at the bell.
This is your MOST IMPORTANT decision of the day.

═══════════════════════════════════════════════════════
YOUR BOOK
═══════════════════════════════════════════════════════
State: {pos_state}
Direction: {pos_direction}
Current P&L: {pos_pnl}

═══════════════════════════════════════════════════════
LAST INTELLIGENCE REPORT
═══════════════════════════════════════════════════════
Stance: {report_stance}
Confidence: {report_conf}%
Rationale: {report_rationale}

═══════════════════════════════════════════════════════
CURRENT PRICE (may be delayed if pre-market)
═══════════════════════════════════════════════════════
Last Price: ${price:.2f}
Day Change: {change:+.2f}%

═══════════════════════════════════════════════════════
OVERNIGHT & PRE-MARKET INTELLIGENCE
═══════════════════════════════════════════════════════
{intel_briefing}

═══════════════════════════════════════════════════════
PRE-MARKET DECISION FRAMEWORK
═══════════════════════════════════════════════════════

Check each of these against the REAL DATA above:

1. HONG KONG TAPE: Has {ticker}'s HK listing already moved overnight?
   If HK is down 3%+, NYSE is likely to open lower. If HK is up, we have tailwind.
   If HK data is not available, note this and proceed with other signals.

2. NEWS WIRE: What happened while NYSE was closed?
   Look at the sentiment scores. Are they skewing bearish or bullish?
   Is there a single dominant story (Pentagon blacklist, regulatory action, earnings)?

3. MACRO CONTEXT: Is S&P indicating risk-off? Is VIX spiking?
   A market-wide sell-off can drag down even strong single-stock theses.

4. DUCK AND COVER?
   If our thesis is STILL VALID long-term but something big will temporarily floor
   the stock — recommend DUCK. We sell at 9:30 ET open, wait for the panic selling
   to exhaust itself, then re-buy around 10:30 ET at a better price.
   Only recommend DUCK if you believe the thesis survives the storm.

Respond in JSON:
{{
    "action": "HOLD|EXIT|DUCK",
    "urgency": "HIGH|MEDIUM|LOW",
    "duck_and_cover": true/false,
    "reason": "2-3 sentences — cite specific data from the wire and HK tape"
}}""".format(
        ticker=ticker,
        pos_state=pos_state,
        pos_direction=pos_direction or 'N/A',
        pos_pnl='{:+.2f}%'.format(pos_pnl) if pos_pnl is not None else 'N/A',
        report_stance=latest_report.get('report_stance', 'N/A') if latest_report else 'N/A',
        report_conf=latest_report.get('report_confidence', 0) if latest_report else 0,
        report_rationale=latest_report.get('report_rationale', 'N/A') if latest_report else 'N/A',
        price=price_data.get('price', 0) if price_data else 0,
        change=price_data.get('change_pct', 0) if price_data else 0,
        intel_briefing=intel_briefing,
    )

    return _call_llm(SYSTEM_PROMPT, prompt, max_tokens=512)


def assess_rebuy(report, price_data, ticker=None):
    """
    After a duck-and-cover sell, assess whether it's safe to re-enter.
    Called ~60 min after market open when initial selling pressure should have eased.

    Returns:
    {
        "action": "REBUY" | "STAY_OUT",
        "house_confidence_pct": 0-100,
        "reason": "explanation"
    }
    """
    ticker = ticker or 'BABA'

    # Get fresh intel for the rebuy decision
    intel = gather_full_intel(ticker)
    intel_briefing = format_intel_briefing(intel, price_data)

    prompt = """DUCK-AND-COVER REBUY ASSESSMENT — {ticker}

We sold at market open to avoid a storm. It's now ~60 minutes after the bell.
Time to decide: get back in, or stay out?

═══════════════════════════════════════════════════════
THE ORIGINAL THESIS
═══════════════════════════════════════════════════════
Report Stance: {stance}
Report Confidence: {conf}%
Rationale: {rationale}

═══════════════════════════════════════════════════════
CURRENT TAPE (60 min after open)
═══════════════════════════════════════════════════════
Price: ${price:.2f}
Day Change: {change:+.2f}%

═══════════════════════════════════════════════════════
LATEST INTELLIGENCE
═══════════════════════════════════════════════════════
{intel_briefing}

═══════════════════════════════════════════════════════
REBUY CRITERIA
═══════════════════════════════════════════════════════
1. Has the selling pressure exhausted itself? (Is the fall slowing/reversing?)
2. Is the original thesis still valid? (Check the news wire)
3. Is the current price BETTER than where we sold? (We profited from ducking)
4. Or has something fundamentally broken and we should stay out?

If the storm has passed and the thesis holds → REBUY
If the situation is worse than expected → STAY_OUT

Respond in JSON:
{{
    "action": "REBUY|STAY_OUT",
    "house_confidence_pct": 0-100,
    "reason": "1-2 sentences — what does the tape and wire tell you?"
}}""".format(
        ticker=ticker,
        stance=report.get('report_stance', 'N/A'),
        conf=report.get('report_confidence', 0),
        rationale=report.get('report_rationale', 'N/A'),
        price=price_data.get('price', 0) if price_data else 0,
        change=price_data.get('change_pct', 0) if price_data else 0,
        intel_briefing=intel_briefing,
    )

    return _call_llm(SYSTEM_PROMPT, prompt, max_tokens=512)
