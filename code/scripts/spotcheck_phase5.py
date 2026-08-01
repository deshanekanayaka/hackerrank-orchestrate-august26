"""Phase 5 validation: the full-pipeline checks that spotcheck_phase1-4.py
don't cover, because they each tested one stage in isolation.

1. validate_output_csv() -- structural check on the dataset/output.csv that
   code/main.py just wrote: right row count, right columns, no blank
   required fields, confidence in [0,1], evidence_message_ids either "none"
   or real message_history.csv ids for that row's user.
2. run_sample_accuracy_with_overrides() -- re-scores sample_messages.csv
   through decide() + apply_safety_overrides() together (Phase 3's spotcheck
   only ever measured decide() alone), since overrides are part of what
   actually ships and can flip a sample row's action.
3. print_spot_check_sample() -- prints a human-readable review card (full
   context block + final decision) for a random, reproducible sample of
   real (non-sample) rows from the output.csv main.py already wrote, so a
   person can eyeball plausibility without cross-referencing 5 CSVs by hand.
   Reads the already-written output.csv rather than re-calling decide() so
   this step costs no extra LLM calls.

Run: `python3 scripts/spotcheck_phase5.py` (from the code/ directory).
Requires OPENAI_API_KEY for step 2 only; steps 1 and 3 need code/main.py to
have been run at least once (dataset/output.csv populated) and need no API
key.
"""

import csv
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.data import DATASET_DIR, load_all
from pipeline.decision import decide_all
from pipeline.format_context import format_message_block
from pipeline.safety import apply_safety_overrides

OUTPUT_PATH = os.path.join(DATASET_DIR, "output.csv")
REQUIRED_COLUMNS = ["message_id", "action", "message_type", "reason", "confidence", "evidence_message_ids"]


def _read_output_rows() -> list[dict]:
    with open(OUTPUT_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validate_output_csv(data):
    print("=" * 90)
    print("Structural validation of dataset/output.csv")
    print("=" * 90)

    if not os.path.exists(OUTPUT_PATH):
        print(f"MISSING: {OUTPUT_PATH} does not exist yet -- run `python3 main.py` first.")
        return False

    rows = _read_output_rows()
    expected_ids = list(data["messages"]["message_id"])
    valid_history_ids_by_user = {
        user_id: set(group["message_id"])
        for user_id, group in data["message_history"].groupby("user_id")
    }

    problems = []

    with open(OUTPUT_PATH, newline="", encoding="utf-8") as f:
        actual_columns = next(csv.reader(f))
    if actual_columns != REQUIRED_COLUMNS:
        problems.append(f"column order mismatch: got {actual_columns}")

    if len(rows) != len(expected_ids):
        problems.append(f"row count mismatch: expected {len(expected_ids)}, got {len(rows)}")

    got_ids = [r["message_id"] for r in rows]
    if got_ids != expected_ids:
        if set(got_ids) == set(expected_ids):
            problems.append("row order differs from messages.csv (same set, different order)")
        else:
            missing = set(expected_ids) - set(got_ids)
            extra = set(got_ids) - set(expected_ids)
            problems.append(f"message_id set mismatch: missing={missing or 'none'} extra={extra or 'none'}")

    user_by_message = dict(zip(data["messages"]["message_id"], data["messages"]["user_id"]))

    for row in rows:
        mid = row["message_id"]
        for field in ("action", "message_type", "reason", "evidence_message_ids"):
            if not (row.get(field) or "").strip():
                problems.append(f"{mid}: blank required field '{field}'")

        try:
            conf = float(row["confidence"])
            if not (0.0 <= conf <= 1.0):
                problems.append(f"{mid}: confidence {conf} out of [0,1] range")
        except (ValueError, TypeError):
            problems.append(f"{mid}: confidence '{row.get('confidence')}' is not numeric")

        evidence = (row.get("evidence_message_ids") or "").strip()
        if evidence and evidence != "none":
            user_id = user_by_message.get(mid)
            valid_ids = valid_history_ids_by_user.get(user_id, set())
            for eid in evidence.split(";"):
                if eid not in valid_ids:
                    problems.append(f"{mid}: evidence id '{eid}' not in this user's message_history.csv")

    if problems:
        print(f"{len(problems)} problem(s) found:")
        for p in problems[:40]:
            print(f"  - {p}")
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
    else:
        print(f"OK -- {len(rows)} rows, columns and row order match messages.csv, no blank fields, "
              "all evidence ids resolve, all confidences in range.")
    return not problems


def run_sample_accuracy_with_overrides(data):
    print("\n" + "=" * 90)
    print("sample_messages.csv accuracy: decide() alone vs decide() + apply_safety_overrides()")
    print("=" * 90)

    samples = data["sample_messages"].to_dict("records")
    raw_decisions = decide_all(samples, data, max_workers=6)

    n = len(samples)
    raw_action_correct = final_action_correct = type_correct = both_correct = 0
    flipped_by_override = 0

    header = f"{'message_id':<16} {'exp':<8} {'raw':<8} {'final':<8} {'exp_type':<16} {'got_type':<16}"
    print(header)
    for row in samples:
        message_id = row["message_id"]
        raw = raw_decisions[message_id]
        final = apply_safety_overrides(row, raw, data)

        exp_action = row["action"]
        raw_ok = exp_action == raw["action"]
        final_ok = exp_action == final["action"]
        type_ok = row["message_type"] == final["message_type"]

        raw_action_correct += raw_ok
        final_action_correct += final_ok
        type_correct += type_ok
        both_correct += final_ok and type_ok
        if raw["action"] != final["action"]:
            flipped_by_override += 1

        flag = ""
        if raw["action"] != final["action"]:
            flag = "  <-- override flipped action"
        elif not final_ok or not type_ok:
            flag = "  <-- MISMATCH"
        print(f"{message_id:<16} {exp_action:<8} {raw['action']:<8} {final['action']:<8} "
              f"{row['message_type']:<16} {final['message_type']:<16}{flag}")

    print(f"\nraw decide() action accuracy:            {raw_action_correct}/{n} ({100*raw_action_correct/n:.1f}%)")
    print(f"final (with overrides) action accuracy:   {final_action_correct}/{n} ({100*final_action_correct/n:.1f}%)")
    print(f"message_type accuracy:                    {type_correct}/{n} ({100*type_correct/n:.1f}%)")
    print(f"both correct (final):                     {both_correct}/{n} ({100*both_correct/n:.1f}%)")
    print(f"rows where a safety override changed the action: {flipped_by_override}/{n}")


def print_spot_check_sample(data, n: int = 18, seed: int = 42):
    print("\n" + "=" * 90)
    print(f"Manual plausibility review: {n} random real rows (seed={seed})")
    print("=" * 90)

    if not os.path.exists(OUTPUT_PATH):
        print(f"MISSING: {OUTPUT_PATH} does not exist yet -- run `python3 main.py` first.")
        return

    output_by_id = {r["message_id"]: r for r in _read_output_rows()}
    messages = data["messages"].to_dict("records")

    rng = random.Random(seed)
    sample = rng.sample(messages, min(n, len(messages)))

    for message in sample:
        mid = message["message_id"]
        decision = output_by_id.get(mid)
        print("\n" + "-" * 90)
        print(f"{mid}  (conversation_type={message['conversation_type']})")
        print("-" * 90)
        print(format_message_block(message, data))
        if decision:
            print("-" * 20)
            print(f"-> action={decision['action']}  message_type={decision['message_type']}  "
                  f"confidence={decision['confidence']}")
            print(f"   reason: {decision['reason']}")
            print(f"   evidence_message_ids: {decision['evidence_message_ids']}")
        else:
            print(f"-> NO output.csv row found for {mid}")


if __name__ == "__main__":
    dataset = load_all()
    ok = validate_output_csv(dataset)
    run_sample_accuracy_with_overrides(dataset)
    print_spot_check_sample(dataset, n=18)

    print("\n" + "=" * 90)
    print("Structural validation: " + ("PASS" if ok else "FAIL -- see problems above"))
    print("=" * 90)
