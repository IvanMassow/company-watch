"""
Company Watch - LLM Due Diligence Engine
Uses OpenAI GPT-4o-mini to:
  1. Assess daily reports with critical thinking
  2. Override report advice when warranted ("human in the loop" AI)
  3. Run autonomous checks between reports
  4. Evaluate profit-taking and loss scenarios
  5. Detect market events that trump the report
"""
import json
import logging

import requests

from config import OPENAI_API_KEY

logger = logging.getLogger("companywatch.llm")

SYSTEM_PROMPT = """You are the due diligence engine for Company Watch, a single-stock intelligence system.

Your role is to act like a SENIOR ANALYST who receives a daily research report and must decide
whether to follow its recommendation, override it, or modify it.

KEY PRINCIPLES:
1. The report is your Bloomberg — it contains narrative signal analysis that catches things
   before mainstream media. It is ADVANCED intelligence, not just news summaries.
2. However, reports can be wrong. Events can overtake a thesis. Markets can move before reports.
3. You are the "human in the loop" — your job is to catch what the report missed.
4. You must consider: Has the horse already bolted? Is there a macro event (like a sudden
   market crash, policy announcement, or geopolitical event) that invalidates the thesis?
5. For dual-listed stocks (e.g. BABA on NYSE and HK), consider whether the Hong Kong
   pre-market has already reacted to news.

WHAT TO CHECK:
- Is the report's thesis still valid given current price action?
- Has the market already priced in what the report is recommending?
- Are there obvious risks the report focused too narrowly to see?
- Is this a good time for profit-taking after a strong run?
- Should we hold through volatility if the thesis is fundamentally sound?

IMPORTANT:
- Do NOT kill a signal just because it's not on Bloomberg. That's the POINT.
- Low confidence (45-55%) doesn't mean bad trade — it means the report is honest about uncertainty.
- HOLD is not failure — it means "wait for clarity."
- You can recommend HOLD even if the report says BUY, and vice versa.
- Be specific about WHY you agree or disagree with the report."""


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


def assess_report(report, price_data, current_position):
    """
    Assess a new daily report and decide whether to follow, modify, or override it.

    Returns:
    {
        "decision": "BUY" | "SELL" | "HOLD" | "FADE",
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "reason": "explanation",
        "agrees_with_report": true/false,
        "override_reason": "why overriding (if applicable)"
    }
    """
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

    prompt = """DAILY REPORT ASSESSMENT

Report Stance: {stance}
Report Confidence: {confidence}%
Report Rationale: {rationale}
Report Watchpoints: {watchpoints}
Report Risks: {risks}
Report Mispricing Notes: {mispricing}

CURRENT POSITION:
- State: {pos_state}
- Direction: {pos_direction}
- Entry Price: {pos_entry}
- Current P&L: {pos_pnl}

CURRENT PRICE DATA:
- Price: ${price:.2f}
- Day Change: {change:.2f}%
- Volume: {volume:,.0f}

Assess this report. Should we follow its recommendation?
Consider: Has the horse bolted? Any macro override? Is this a profit-taking opportunity?

IMPORTANT: You must provide your OWN house confidence percentage (0-100).
This can be higher than the report (you're MORE confident because your research confirms it)
or lower (you're LESS confident because something worries you).
For example: report says HOLD 62%, but your research says actually BUY 83%.
Or: report says BUY 70%, but you think SELL with house confidence -40% (negative = opposite direction).

Respond in JSON:
{{
    "decision": "BUY|SELL|HOLD|FADE",
    "confidence": "HIGH|MEDIUM|LOW",
    "house_confidence_pct": 0-100,
    "reason": "2-3 sentence explanation",
    "agrees_with_report": true/false,
    "override_reason": "if disagreeing, why"
}}""".format(
        stance=report.get('report_stance', 'UNKNOWN'),
        confidence=report.get('report_confidence', 0),
        rationale=report.get('report_rationale', 'N/A'),
        watchpoints=report.get('report_watchpoints', 'N/A'),
        risks=report.get('report_risks', 'N/A'),
        mispricing=report.get('report_mispricing', 'N/A'),
        pos_state=pos_state,
        pos_direction=pos_direction or 'N/A',
        pos_entry='${:.2f}'.format(pos_entry) if pos_entry else 'N/A',
        pos_pnl='{:.2f}%'.format(pos_pnl) if pos_pnl is not None else 'N/A',
        price=price_data.get('price', 0) if price_data else 0,
        change=price_data.get('change_pct', 0) if price_data else 0,
        volume=price_data.get('volume', 0) if price_data else 0,
    )

    return _call_llm(SYSTEM_PROMPT, prompt)


def autonomous_check(current_position, price_data, latest_report):
    """
    Autonomous check between reports.
    Looks for reasons to exit or reverse that the daily report might have missed.

    Returns:
    {
        "action": "HOLD" | "EXIT" | "REVERSE",
        "reason": "explanation",
        "urgency": "HIGH" | "MEDIUM" | "LOW"
    }
    """
    if not current_position or current_position.get('state') == 'FLAT':
        return None

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

    prompt = """AUTONOMOUS DUE DILIGENCE CHECK

CURRENT POSITION:
- Direction: {direction}
- Entry: ${entry:.2f}
- Current: ${price:.2f}
- P&L: {pnl:.2f}%
- Peak P&L: {peak_pnl:.2f}%
- Peak Price: ${peak:.2f}

LATEST REPORT:
- Stance: {report_stance}
- Confidence: {report_conf}%
- Age: {report_age}
- Rationale: {report_rationale}

CURRENT PRICE ACTION:
- Day Change: {change:.2f}%

You are checking BETWEEN daily reports. Look for:
1. Has anything changed that the morning report couldn't have known?
2. Should we protect profits after a strong run?
3. Is the loss getting too deep to hold through?
4. Any sign the thesis has been invalidated by market action?

If everything looks fine, say HOLD. Only recommend EXIT or REVERSE if genuinely warranted.

Respond in JSON:
{{
    "action": "HOLD|EXIT|REVERSE",
    "reason": "1-2 sentence explanation",
    "urgency": "HIGH|MEDIUM|LOW"
}}""".format(
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
    )

    return _call_llm(SYSTEM_PROMPT, prompt, max_tokens=512)


def assess_loss(current_position, price_data, latest_report):
    """
    Called when position is at a soft stop-loss level.
    Decides whether to cut or hold through the pain.

    Returns:
    {
        "action": "HOLD" | "EXIT",
        "reason": "explanation"
    }
    """
    direction = current_position.get('direction', 'LONG')
    entry_price = current_position.get('entry_price', 0)
    price = price_data.get('price', 0) if price_data else 0

    from tracker import calculate_pnl
    current_pnl = calculate_pnl(entry_price, price, direction)

    prompt = """LOSS ASSESSMENT

Position: {direction} from ${entry:.2f}
Current: ${price:.2f}
P&L: {pnl:.2f}%

Latest Report Stance: {stance} (Confidence: {conf}%)
Report Rationale: {rationale}

This position has hit the soft stop-loss zone. Should we:
1. EXIT - Cut losses, thesis is damaged
2. HOLD - Thesis still valid, this is temporary pain

Consider: Is the report's thesis fundamentally broken, or is this normal volatility?
A position with a valid thesis can recover. A broken thesis won't.

Respond in JSON:
{{
    "action": "HOLD|EXIT",
    "reason": "1-2 sentence explanation"
}}""".format(
        direction=direction,
        entry=entry_price,
        price=price,
        pnl=current_pnl,
        stance=latest_report.get('report_stance', 'N/A') if latest_report else 'N/A',
        conf=latest_report.get('report_confidence', 0) if latest_report else 0,
        rationale=latest_report.get('report_rationale', 'N/A') if latest_report else 'N/A',
    )

    return _call_llm(SYSTEM_PROMPT, prompt, max_tokens=512)


def premarket_check(current_position, price_data, latest_report):
    """
    Pre-market DD: runs BEFORE NYSE opens.
    Checks HK tape, overnight news, and whether to duck-and-cover at open.

    Returns:
    {
        "action": "HOLD" | "EXIT" | "DUCK",
        "urgency": "HIGH" | "MEDIUM" | "LOW",
        "duck_and_cover": true/false,
        "reason": "explanation"
    }
    """
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

    prompt = """PRE-MARKET DUE DILIGENCE

This is your MOST IMPORTANT job before NYSE opens. You are checking overnight conditions.

CURRENT POSITION:
- State: {pos_state}
- Direction: {pos_direction}
- Current P&L: {pos_pnl}

LATEST REPORT:
- Stance: {report_stance}
- Confidence: {report_conf}%
- Rationale: {report_rationale}

CURRENT PRICE (may be delayed):
- Last Price: ${price:.2f}
- Day Change: {change:.2f}%

PRE-MARKET CHECKS TO PERFORM:
1. Has the Hong Kong market (where BABA also trades as 9988.HK) already reacted overnight?
2. Are there any major overnight news events that change the thesis?
3. Is there a macro storm coming (US-China tensions, Fed announcements, earnings surprises)?
4. Should we DUCK AND COVER? (sell at market open 9:30 ET, wait for the storm to pass,
   then re-buy around 10:30 ET when the panic selling is done)

Duck-and-cover is for when: our thesis is STILL VALID long-term, but something bigger
is going to floor the stock temporarily. We want to sell at 9:30 and re-buy at 10:30
when the wind has been taken out of the fall.

Respond in JSON:
{{
    "action": "HOLD|EXIT|DUCK",
    "urgency": "HIGH|MEDIUM|LOW",
    "duck_and_cover": true/false,
    "reason": "2-3 sentence explanation"
}}""".format(
        pos_state=pos_state,
        pos_direction=pos_direction or 'N/A',
        pos_pnl='{:.2f}%'.format(pos_pnl) if pos_pnl is not None else 'N/A',
        report_stance=latest_report.get('report_stance', 'N/A') if latest_report else 'N/A',
        report_conf=latest_report.get('report_confidence', 0) if latest_report else 0,
        report_rationale=latest_report.get('report_rationale', 'N/A') if latest_report else 'N/A',
        price=price_data.get('price', 0) if price_data else 0,
        change=price_data.get('change_pct', 0) if price_data else 0,
    )

    return _call_llm(SYSTEM_PROMPT, prompt, max_tokens=512)


def assess_rebuy(report, price_data):
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
    prompt = """DUCK-AND-COVER REBUY ASSESSMENT

We sold at market open to avoid a storm. It's now ~60 minutes after open.
Should we re-enter the position?

REPORT:
- Stance: {stance}
- Confidence: {conf}%
- Rationale: {rationale}

CURRENT PRICE:
- Price: ${price:.2f}
- Day Change: {change:.2f}%

Consider:
1. Has the selling pressure eased?
2. Is the original thesis still valid?
3. Is the price now BETTER than where we sold (we made money by ducking)?
4. Or has something fundamentally changed and we should stay out?

Respond in JSON:
{{
    "action": "REBUY|STAY_OUT",
    "house_confidence_pct": 0-100,
    "reason": "1-2 sentence explanation"
}}""".format(
        stance=report.get('report_stance', 'N/A'),
        conf=report.get('report_confidence', 0),
        rationale=report.get('report_rationale', 'N/A'),
        price=price_data.get('price', 0) if price_data else 0,
        change=price_data.get('change_pct', 0) if price_data else 0,
    )

    return _call_llm(SYSTEM_PROMPT, prompt, max_tokens=512)
