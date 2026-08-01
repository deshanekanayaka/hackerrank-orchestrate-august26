"""Stage 3 decision engine: message + context -> structured routing decision
(FR-2 through FR-8).

Uses OpenAI structured outputs (strict json_schema) so action/message_type
are enum-constrained by the API itself -- the response literally cannot use
a category outside the allowed set, so no shape-repair/parse-retry is
needed. The only retry here is for transport-level failures (timeout,
rate-limit, 5xx); if those are exhausted, decide() returns a clearly-
flagged fallback row rather than raising, so one bad row never crashes a
batch run (see decide_all()).

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


def decide(message: dict, data: dict, max_retries: int = 2) -> dict:
    """Returns {action, message_type, reason, confidence, evidence_message_ids}.

    evidence_message_ids is a list[str] here; the CSV-format join into a
    semicolon-separated string (or "none") happens at the output-writer
    boundary (Phase 5), not here, so Phase 4 can still filter the list.
    """
    user_block = format_message_block(message, data)
    system_prompt = _system_prompt(data)

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            resp = _get_client().chat.completions.create(
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
                time.sleep(2**attempt)

    return _fallback_decision(str(last_error))


def decide_all(messages: list[dict], data: dict, max_workers: int = 6) -> dict:
    """Runs decide() concurrently across many message rows, keyed by message_id.

    5-8 worker range recommended: batch job over ~110 rows, no queue/backoff
    infra needed at this scale (PRD non-goals). decide() never raises, so a
    single row's failure can't take down the pool.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(decide, m, data): m["message_id"] for m in messages}
        for future in as_completed(futures):
            message_id = futures[future]
            results[message_id] = future.result()
    return results
