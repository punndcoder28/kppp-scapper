"""
Claude haiku-4-5 based tender evaluator.

Classifies each tender into one of five priority tiers:
  lab_equipment  > construction  > other_supply  > low_priority  > skip
"""

import asyncio
import json
import logging
import os
import re

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1500       # ~25 items × ~42 tokens each, with headroom
BATCH_SIZE = int(os.getenv("TENDER_BATCH_SIZE", "25"))
BATCH_DELAY = 7.0       # seconds between batches — keeps output under 10k TPM

VALID_LABELS = {"lab_equipment", "construction", "other_supply", "low_priority", "skip"}

SYSTEM_PROMPT = """You are evaluating Karnataka government tenders for a company that:
1. Supplies laboratory and scientific equipment to educational institutions and government departments.
2. Does civil construction / building work for government clients.
3. Can also supply general physical goods/equipment (e.g. sewing machines, furniture) if relevant.

Classify each tender with exactly one label:
- "lab_equipment": Supply of lab instruments, scientific apparatus, glassware, chemicals, microscopes, spectrophotometers, or any equipment destined for colleges, universities, research institutes, or government science/medical departments.
- "construction": Civil engineering work, building construction, structural renovation, electrical/plumbing works as part of a construction contract for government clients.
- "other_supply": Supply of any other physical goods or equipment the company could potentially fulfill — sewing machines, furniture, general equipment. NOT IT equipment or stationery.
- "low_priority": IT equipment (computers, printers, servers, networking gear) or stationery (paper, pens, books, printed materials). Possible but unlikely fit.
- "skip": Clearly irrelevant — vehicles, food supply, uniforms, printing services, software, maintenance/repair services, consultancy.

Respond ONLY with a JSON array (no markdown fences), one object per tender in input order:
[{"index": 0, "label": "lab_equipment", "reason": "one sentence"}, ...]"""


async def evaluate_tenders(tenders: list[dict]) -> list[dict]:
    """Evaluate all tenders and return a parallel list of evaluation dicts.

    Each evaluation dict: {"label": str, "reason": str}
    Order matches the input tenders list.
    """
    if not tenders:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")

    client = anthropic.AsyncAnthropic(api_key=api_key)

    batches = [tenders[i : i + BATCH_SIZE] for i in range(0, len(tenders), BATCH_SIZE)]
    log.info(f"Evaluating {len(tenders)} tenders in {len(batches)} batches (sequential, {BATCH_DELAY}s delay)")

    evaluations: list[dict] = []
    for i, batch in enumerate(batches):
        if i > 0:
            await asyncio.sleep(BATCH_DELAY)
        log.debug(f"Batch {i + 1}/{len(batches)}")
        result = await _evaluate_batch(client, batch)
        evaluations.extend(result)

    return evaluations


async def _evaluate_batch(client: anthropic.AsyncAnthropic, batch: list[dict]) -> list[dict]:
    user_message = _build_prompt(batch)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text
        return _parse_response(text, len(batch))
    except Exception as e:
        log.warning(f"Claude API call failed: {e}")
        return [{"label": "other_supply", "reason": "api error"} for _ in batch]


def _build_prompt(batch: list[dict]) -> str:
    lines = [f"Evaluate these {len(batch)} tenders:\n"]
    for i, t in enumerate(batch):
        desc = (t.get("description") or "")[:200]
        ecv = _format_ecv_short(t.get("ecv"))
        lines.append(
            f"[{i}] Title: {t.get('title', 'N/A')}\n"
            f"     Department: {t.get('department', 'N/A')}\n"
            f"     Category: {t.get('category', 'N/A')}\n"
            f"     ECV: {ecv}\n"
            f"     Description: {desc or 'N/A'}\n"
        )
    return "\n".join(lines)


def _parse_response(text: str, batch_size: int) -> list[dict]:
    text = text.strip()

    # Always extract the JSON array directly — handles fenced and unfenced responses
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        log.warning(f"No JSON array found in Claude response; defaulting batch.\nResponse: {text[:300]}")
        return [{"label": "other_supply", "reason": "parse error"} for _ in range(batch_size)]

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as e:
        log.warning(f"Could not parse Claude response as JSON ({e}); defaulting batch.\nResponse: {text[:300]}")
        return [{"label": "other_supply", "reason": "parse error"} for _ in range(batch_size)]

    if not isinstance(data, list):
        log.warning("Claude response is not a list; defaulting batch")
        return [{"label": "other_supply", "reason": "unexpected format"} for _ in range(batch_size)]

    result: list[dict] = [{"label": "other_supply", "reason": "missing"} for _ in range(batch_size)]
    for item in data:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        label = item.get("label", "other_supply")
        reason = item.get("reason", "")
        if not isinstance(idx, int) or idx < 0 or idx >= batch_size:
            continue
        if label not in VALID_LABELS:
            label = "other_supply"
        result[idx] = {"label": label, "reason": reason}

    return result


def _format_ecv_short(ecv: float | None) -> str:
    if not ecv:
        return "N/A"
    if ecv >= 1e7:
        return f"₹{ecv / 1e7:.2f} Cr"
    if ecv >= 1e5:
        return f"₹{ecv / 1e5:.2f} L"
    return f"₹{ecv:,.0f}"
