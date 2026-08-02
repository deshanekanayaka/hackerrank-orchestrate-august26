"""Entry point: runs the full pipeline over dataset/messages.csv and writes
dataset/output.csv (FR-1).

    messages.csv -> [context + evidence] -> [decision engine] -> [safety
    override] -> [CSV writer] -> output.csv

Context assembly (Phase 1) and media grounding (Phase 2) aren't called
directly here -- decide() pulls them in itself via format_context.py /
media.py, keyed off each message dict. This script is just the outer loop:
load data, run decide_all() (Phase 3), run apply_safety_overrides() per row
(Phase 4), format evidence_message_ids into the sample_messages.csv-style
CSV string (semicolon-joined, or the literal string "none" when empty), and
write the row in the same order dataset/output.csv's own template already
uses (verified identical to messages.csv's row order).

Run: `python3 code/main.py` (from the repo root; requires OPENAI_API_KEY,
see .env). Expected runtime: ~110 messages / 6 workers, dominated by the LLM
+ media API calls -- a few minutes on a warm media cache, longer on a cold
one (first-ever run OCRs/transcribes every image/voice row).

decide_all() already guarantees a decision dict per row (decide() catches
its own per-row failures and returns a fallback rather than raising -- see
decision.py). apply_safety_overrides() does its own pandas lookups and a
second ground_message() call per row, though, and was not similarly
guarded: an exception there used to propagate straight out of this loop
and abort the whole batch, producing zero rows for every message instead
of one degraded row for the message that triggered it (Phase 6 robustness
pass). The per-row try/except below is the fix -- it mirrors decide.py's
_fallback_decision shape so a batch-ending exception in the safety stage
degrades to the same kind of safe, clearly-flagged row a batch-ending
exception in the decision stage already produces.
"""

import csv
import os
import traceback

from pipeline.data import DATASET_DIR, load_all
from pipeline.decision import decide_all
from pipeline.safety import apply_safety_overrides

OUTPUT_PATH = os.path.join(DATASET_DIR, "output.csv")
COLUMNS = ["message_id", "action", "message_type", "reason", "confidence", "evidence_message_ids"]


def format_evidence(evidence_message_ids: list[str]) -> str:
    return ";".join(evidence_message_ids) if evidence_message_ids else "none"


def _fallback_row(message_id: str, error: str) -> dict:
    return {
        "message_id": message_id,
        "action": "digest",
        "message_type": "unknown",
        "reason": f"Safety override stage failed for this row ({error}); defaulted to a safe low-confidence digest.",
        "confidence": 0.1,
        "evidence_message_ids": "none",
    }


def run() -> list[dict]:
    data = load_all()
    messages = data["messages"].to_dict("records")

    print(f"Running decision engine over {len(messages)} messages...")
    decisions = decide_all(messages, data)

    rows = []
    for message in messages:
        message_id = message.get("message_id", f"unknown_row_{len(rows)}")
        try:
            decision = apply_safety_overrides(message, decisions[message_id], data)
            rows.append(
                {
                    "message_id": message_id,
                    "action": decision["action"],
                    "message_type": decision["message_type"],
                    "reason": decision["reason"],
                    "confidence": decision["confidence"],
                    "evidence_message_ids": format_evidence(decision["evidence_message_ids"]),
                }
            )
        except Exception as e:
            print(f"  WARNING: safety override failed for {message_id}: {e}")
            traceback.print_exc()
            rows.append(_fallback_row(message_id, str(e)))

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")
    return rows


if __name__ == "__main__":
    run()
