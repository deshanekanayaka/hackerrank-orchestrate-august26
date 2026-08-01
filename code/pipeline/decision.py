"""Stage 3 decision engine: message + context -> structured routing decision
(FR-2 through FR-8).

Uses OpenAI structured outputs (strict json_schema) so action/message_type
are enum-constrained by the API itself -- the response literally cannot use
a category outside the allowed set, so no shape-repair/parse-retry is
needed. The only retry here is for transport-level failures (timeout,
rate-limit, 5xx); if those are exhausted, decide() returns a clearly-
flagged fallback row rather than raising, so one bad row (or one malformed
message dict) never crashes a batch run (see decide_all()). The one
exception is a missing OPENAI_API_KEY: that is a misconfiguration, not a
per-row failure, and is left to propagate out of decide()/decide_all()
so it fails fast and loud instead of being masked as N per-row fallbacks.

evidence_message_ids returned here are the model's picks from the candidate
list shown in the prompt (see format_context.py) -- Stage 3 only asks it to
stay within that list, it does not guarantee it. Phase 4's validation
against message_history.csv is the actual hallucination backstop.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from openai import OpenAI

from .format_context import format_message_block
from .prompts import build_system_prompt

load_dotenv()

DECISION_MODEL = os.environ.get("DECISION_MODEL", "gpt-4o-mini")

ACTIONS = ["notify", "digest", "mute"]
MESSAGE_TYPES = [
    "personal",
    "urgent",
    "event",
    "payment",
    "business_update",
    "promotion",
    "greeting",
    "forward",
    "spam",
    "scam",
    "unknown",
]

RESPONSE_SCHEMA = {
    "name": "routing_decision",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ACTIONS},
            "message_type": {"type": "string", "enum": MESSAGE_TYPES},
            "reason": {"type": "string"},
            "confidence": {"type": "number"},
            "evidence_message_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["action", "message_type", "reason", "confidence", "evidence_message_ids"],
        "additionalProperties": False,
    },
}

_client = None
_system_prompt_cache = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Add it to .env or export it before "
                "running the decision engine."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def _system_prompt(data: dict) -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        _system_prompt_cache = build_system_prompt(data)
    return _system_prompt_cache


def _fallback_decision(error: str) -> dict:
    return {
        "action": "digest",
        "message_type": "unknown",
        "reason": f"Decision engine call failed after retries ({error}); defaulted to a safe low-confidence digest.",
        "confidence": 0.1,
        "evidence_message_ids": [],
    }


def decide(message: dict, data: dict, max_retries: int = 4) -> dict:
    """Returns {action, message_type, reason, confidence, evidence_message_ids}.

    evidence_message_ids is a list[str] here; the CSV-format join into a
    semicolon-separated string (or "none") happens at the output-writer
    boundary (Phase 5), not here, so Phase 4 can still filter the list.
    """
    try:
        user_block = format_message_block(message, data)
        system_prompt = _system_prompt(data)
    except Exception as e:
        # Deterministic formatting failure (e.g. a malformed row) -- retrying
        # would hit the same error every time, so fall back immediately.
        return _fallback_decision(str(e))

    # Resolved once, outside the retry loop: a missing OPENAI_API_KEY is a
    # misconfiguration, not a transient per-row failure. Let it propagate
    # instead of being retried and silently swallowed into a fallback row
    # for every message in the batch.
    client = _get_client()

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=DECISION_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Message to route:\n\n{user_block}"},
                ],
                response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
                temperature=0,
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                # A 429 (TPM cap) needs longer than a transient-network-error
                # backoff would: with several workers retrying in lockstep,
                # a short 1-2s backoff lets them collide on the same limit
                # again immediately (observed empirically: 19/110 rows in a
                # real full run exhausted 2 retries at 1s/2s and fell back).
                # Base it in seconds, not fractions, and cap it so a truly
                # persistent failure still gives up within a few tries.
                time.sleep(min(4 * (2**attempt), 30))

    return _fallback_decision(str(last_error))


def decide_all(messages: list[dict], data: dict, max_workers: int = 4) -> dict:
    """Runs decide() concurrently across many message rows, keyed by message_id.

    Lowered from the original 6 to 4 after a real full run over all 110
    messages.csv rows showed 6 concurrent large prompts (system prompt +
    few-shot + per-row context, some rows with 10 evidence candidates)
    collectively exceeding the account's 200k TPM cap for gpt-4o-mini --
    19/110 rows hit repeated 429s and fell back before the shorter backoff
    that existed at the time could recover. No queue/backoff-at-scale infra
    added (PRD non-goals) -- just a smaller worker pool plus the longer
    per-attempt backoff in decide(). decide() never raises for a per-row/
    transient failure, so one bad row can't take down the pool -- but a
    missing OPENAI_API_KEY propagates out of the first future.result() call
    below, since that's a misconfiguration affecting every row, not one.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(decide, m, data): m["message_id"] for m in messages}
        for future in as_completed(futures):
            message_id = futures[future]
            results[message_id] = future.result()
    return results
