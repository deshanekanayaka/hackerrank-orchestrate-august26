"""Phase 3 validation: run the decision engine over every sample_messages.csv
row (known-correct action/message_type) and report accuracy, then exercise
the edge cases called out in tasks.md Phase 3 (empty message_text, missing
group_id, missing media file) to confirm decide() never crashes on them.

Run from the code/ directory (or anywhere, paths resolve relative to this
file): `python3 scripts/spotcheck_phase3.py`. Requires OPENAI_API_KEY.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.data import load_all
from pipeline.decision import decide, decide_all


def run_sample_accuracy(data):
    print("=" * 90)
    print("sample_messages.csv accuracy (action / message_type)")
    print("=" * 90)

    samples = data["sample_messages"].to_dict("records")
    decisions = decide_all(samples, data, max_workers=6)

    action_correct = 0
    type_correct = 0
    both_correct = 0
    print(f"{'message_id':<16} {'exp_action':<9} {'got_action':<9} {'exp_type':<16} {'got_type':<16}")
    for row in samples:
        message_id = row["message_id"]
        decision = decisions[message_id]
        exp_action, got_action = row["action"], decision["action"]
        exp_type, got_type = row["message_type"], decision["message_type"]
        action_ok = exp_action == got_action
        type_ok = exp_type == got_type
        action_correct += action_ok
        type_correct += type_ok
        both_correct += action_ok and type_ok
        flag = "" if action_ok and type_ok else "  <-- MISMATCH"
        print(f"{message_id:<16} {exp_action:<9} {got_action:<9} {exp_type:<16} {got_type:<16}{flag}")

    n = len(samples)
    print(f"\naction accuracy:       {action_correct}/{n} ({100 * action_correct / n:.1f}%)")
    print(f"message_type accuracy: {type_correct}/{n} ({100 * type_correct / n:.1f}%)")
    print(f"both correct:          {both_correct}/{n} ({100 * both_correct / n:.1f}%)")


def run_edge_cases(data):
    print("\n" + "=" * 90)
    print("Robustness: edge cases must not crash decide()")
    print("=" * 90)

    any_user_id = data["messages"].iloc[0]["user_id"]
    base = {
        "user_id": any_user_id,
        "sender_user_id": "u_999",
        "created_at": "2026-07-31 09:00",
        "media_type": "",
        "media_id": "",
        "forwarded_count": "0",
    }

    empty_text = {**base, "message_id": "edge_empty_text", "conversation_type": "personal",
                  "group_id": "", "business_id": "", "message_text": ""}
    d = decide(empty_text, data)
    print("empty message_text, no media          -> ok:", d["action"], d["message_type"])

    missing_group = {**base, "message_id": "edge_missing_group", "conversation_type": "group",
                      "group_id": "group_does_not_exist", "business_id": "", "message_text": "test"}
    d = decide(missing_group, data)
    print("group_id with no groups.csv row        -> ok:", d["action"], d["message_type"])

    missing_media = {**base, "message_id": "edge_missing_media", "conversation_type": "personal",
                      "group_id": "", "business_id": "", "message_text": "",
                      "media_type": "image", "media_id": "img_does_not_exist"}
    d = decide(missing_media, data)
    print("missing media file (bad media_id)      -> ok:", d["action"], d["message_type"])


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        from dotenv import load_dotenv

        load_dotenv()
    dataset = load_all()
    run_sample_accuracy(dataset)
    run_edge_cases(dataset)
