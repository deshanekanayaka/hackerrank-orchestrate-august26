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
"""

import csv
import os

from pipeline.data import DATASET_DIR, load_all
from pipeline.decision import decide_all
from pipeline.safety import apply_safety_overrides

OUTPUT_PATH = os.path.join(DATASET_DIR, "output.csv")
COLUMNS = ["message_id", "action", "message_type", "reason", "confidence", "evidence_message_ids"]


def format_evidence(evidence_message_ids: list[str]) -> str:
    return ";".join(evidence_message_ids) if evidence_message_ids else "none"


def run() -> list[dict]:
    data = load_all()
    messages = data["messages"].to_dict("records")

    print(f"Running decision engine over {len(messages)} messages...")
    decisions = decide_all(messages, data)

    rows = []
    for message in messages:
        message_id = message["message_id"]
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

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")
    return rows


if __name__ == "__main__":
    run()
