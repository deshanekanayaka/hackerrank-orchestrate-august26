"""Phase 6 validation: robustness pass over the 5 tasks.md Phase 6 checkpoints.

  1. Empty message_text handled (with and without media)
  2. Missing media file (referenced but absent) handled without crash
  3. forwarded_count signal actually used in reasoning (prompts.py rule 11)
  4. Pure personal conversation_type (no group/business context) handled
  5. One bad row doesn't crash the batch; every message_id still gets a row

Checks 2, 3 (rule-presence part), and 5 are fully deterministic -- no
OPENAI_API_KEY needed, same split as spotcheck_phase4.py. Checks 1, 4, and
3's live A/B comparison call decide() for real and are skipped with a note
if OPENAI_API_KEY isn't set, same as spotcheck_phase3.py/phase5.py.

Run: `python3 scripts/spotcheck_phase6.py` (from the code/ directory).
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.context import assemble_context
from pipeline.data import load_all
from pipeline.decision import decide
from pipeline.media import ground_message
from pipeline.prompts import RULES
from pipeline.safety import CONFIDENCE_CAP, apply_safety_overrides

PASS = "PASS"
FAIL = "FAIL"
_results = []


def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    _results.append(condition)
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))


def has_api_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def run_empty_text_cases(data):
    print("=" * 90)
    print("1. Empty message_text handled (with and without media)")
    print("=" * 90)
    if not has_api_key():
        print("(skipped -- OPENAI_API_KEY not set; decide() needs a live call)")
        return

    any_user_id = data["messages"].iloc[0]["user_id"]
    base = {
        "user_id": any_user_id,
        "sender_user_id": "u_999",
        "created_at": "2026-07-31 09:00",
        "forwarded_count": "0",
    }

    no_media = {
        **base,
        "message_id": "p6_empty_no_media",
        "conversation_type": "personal",
        "group_id": "",
        "business_id": "",
        "message_text": "",
        "media_type": "",
        "media_id": "",
    }
    d = decide(no_media, data)
    check(
        "empty text, no media -> decide() returns a valid decision without raising",
        d["action"] in ("notify", "digest", "mute"),
        f"got action={d['action']}",
    )

    real_image_id = data["images"].iloc[0]["image_id"] if not data["images"].empty else ""
    with_media = {
        **base,
        "message_id": "p6_empty_with_media",
        "conversation_type": "personal",
        "group_id": "",
        "business_id": "",
        "message_text": "",
        "media_type": "image",
        "media_id": real_image_id,
    }
    d = decide(with_media, data)
    check(
        "empty text WITH real image media -> decide() returns a valid decision without raising",
        d["action"] in ("notify", "digest", "mute"),
        f"got action={d['action']}",
    )


def run_missing_media_case(data):
    print("\n" + "=" * 90)
    print("2. Missing media file (referenced but absent) handled without crash")
    print("=" * 90)

    no_row_message = {
        "user_id": data["messages"].iloc[0]["user_id"],
        "conversation_type": "personal",
        "group_id": "",
        "business_id": "",
        "sender_user_id": "u_999",
        "message_text": "",
        "forwarded_count": "0",
        "media_type": "image",
        "media_id": "img_does_not_exist_p6",
    }
    media = ground_message(no_row_message, data)
    check(
        "ground_message() does not raise for a media_id with no images.csv row at all",
        True,
    )
    check("media_error is set (not silently swallowed)", bool(media["media_error"]))

    generous = {
        "action": "notify",
        "message_type": "personal",
        "reason": "test",
        "confidence": 0.9,
        "evidence_message_ids": [],
    }
    decision = apply_safety_overrides(
        {**no_row_message, "message_id": "p6_missing_media_no_row"}, generous, data
    )
    check(
        f"confidence capped at <= {CONFIDENCE_CAP} when media_id has no images.csv row",
        decision["confidence"] <= CONFIDENCE_CAP,
        f"got {decision['confidence']}",
    )

    # Second scenario: the media_id DOES resolve in images.csv (a real row),
    # but the file itself is absent from disk -- media.py's other missing-
    # file branch (media.py:124-125). Every real image_id in images.csv is
    # already cached from prior full runs, so reusing one would hit the
    # cache and never touch the file-existence check at all; instead inject
    # a synthetic images.csv row (never cached) pointing at a path that
    # genuinely doesn't exist on disk, on a throwaway copy of data so the
    # shared dataset dict isn't mutated for later checks.
    import pandas as pd

    synthetic_row = pd.DataFrame(
        [{"image_id": "img_p6_missing_disk", "file_path": "media/images/img_p6_missing_disk.jpg"}]
    )
    data_with_synthetic_row = {**data, "images": pd.concat([data["images"], synthetic_row])}
    on_disk_message = {
        **no_row_message,
        "media_id": "img_p6_missing_disk",
        "message_id": "p6_missing_media_on_disk",
    }
    media2 = ground_message(on_disk_message, data_with_synthetic_row)
    check(
        "media file referenced in images.csv but absent on disk -> media_error set, no crash",
        bool(media2["media_error"]),
        f"error={media2['media_error']!r}",
    )


def run_forwarded_count_rule_check(data):
    print("\n" + "=" * 90)
    print("3. forwarded_count signal actually used in reasoning")
    print("=" * 90)
    check(
        "prompts.py RULES explicitly instructs the model how to weigh forwarded_count",
        "forwarded count" in RULES.lower(),
    )

    if not has_api_key():
        print("(informational live A/B comparison skipped -- OPENAI_API_KEY not set)")
        return

    any_user_id = data["messages"].iloc[0]["user_id"]
    base = {
        "user_id": any_user_id,
        "sender_user_id": "u_999",
        "conversation_type": "personal",
        "group_id": "",
        "business_id": "",
        "created_at": "2026-07-31 09:00",
        "media_type": "",
        "media_id": "",
        "message_text": "Amazing deal!! Click here to claim your free prize now, limited time only!!!",
    }
    low = {**base, "message_id": "p6_fwd_low", "forwarded_count": "0"}
    high = {**base, "message_id": "p6_fwd_high", "forwarded_count": "87"}
    d_low = decide(low, data)
    d_high = decide(high, data)
    print(f"  forwarded_count=0  -> action={d_low['action']}, type={d_low['message_type']}")
    print(f"  forwarded_count=87 -> action={d_high['action']}, type={d_high['message_type']}")
    print(
        "  (informational only -- not scored pass/fail; a single signal isn't "
        "guaranteed to flip the outcome and shouldn't be hand-tuned to, per "
        "the same overfitting concern already documented for Phase 3.)"
    )


def run_pure_personal_case(data):
    print("\n" + "=" * 90)
    print("4. Pure personal conversation_type (no group/business context) handled")
    print("=" * 90)
    personal_rows = data["messages"][data["messages"]["conversation_type"] == "personal"]
    if personal_rows.empty:
        print("(skipped -- no personal-conversation_type row in messages.csv)")
        return
    message = personal_rows.iloc[0].to_dict()
    check(
        "real personal-conversation_type row has no group_id/business_id",
        not (message.get("group_id") or "").strip() and not (message.get("business_id") or "").strip(),
    )

    ctx = assemble_context(message, data)
    check(
        "context assembly returns no group/business context for a personal message",
        ctx["group"] is None and ctx["business"] is None,
    )

    if not has_api_key():
        print("(skipped live decide() check -- OPENAI_API_KEY not set)")
        return
    d = decide(message, data)
    check(
        "decide() returns a valid decision for a pure personal message",
        d["action"] in ("notify", "digest", "mute"),
        f"got action={d['action']}",
    )


def run_batch_crash_isolation_case(data):
    print("\n" + "=" * 90)
    print("5. One bad row doesn't crash the batch; every message_id still gets a row")
    print("=" * 90)
    # No API key needed -- decide_all() and apply_safety_overrides() are both
    # mocked/patched so this exercises only main.py's per-row loop.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import main as main_module
    from pipeline import safety as safety_module

    messages = data["messages"].to_dict("records")
    poison_id = messages[len(messages) // 2]["message_id"]

    canned_decisions = {
        m["message_id"]: {
            "action": "notify",
            "message_type": "personal",
            "reason": "canned decision for robustness test",
            "confidence": 0.8,
            "evidence_message_ids": [],
        }
        for m in messages
    }

    real_apply = safety_module.apply_safety_overrides

    def poisoned_apply(message, decision, data_arg):
        if message["message_id"] == poison_id:
            raise RuntimeError("synthetic safety-stage failure for robustness test")
        return real_apply(message, decision, data_arg)

    tmp_output = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_phase6_output_test.csv"
    )

    try:
        with patch("main.decide_all", return_value=canned_decisions), patch(
            "main.apply_safety_overrides", side_effect=poisoned_apply
        ), patch("main.OUTPUT_PATH", tmp_output):
            rows = main_module.run()
    finally:
        if os.path.exists(tmp_output):
            os.remove(tmp_output)

    check("run() completed without an exception escaping despite one poisoned row", True)
    check("every message_id still got exactly one row", len(rows) == len(messages))

    row_by_id = {r["message_id"]: r for r in rows}
    check("the poisoned row is present in the output", poison_id in row_by_id)
    check(
        "the poisoned row degraded to a safe fallback (action=digest)",
        row_by_id.get(poison_id, {}).get("action") == "digest",
        f"got {row_by_id.get(poison_id)}",
    )
    check(
        "the poisoned row has low confidence",
        float(row_by_id.get(poison_id, {}).get("confidence", 1.0)) <= 0.2,
    )
    other_id = next(m["message_id"] for m in messages if m["message_id"] != poison_id)
    check(
        "a non-poisoned row is untouched by the poisoning (still the canned decision)",
        row_by_id[other_id]["action"] == "notify",
        f"got {row_by_id[other_id]}",
    )


if __name__ == "__main__":
    dataset = load_all()
    run_empty_text_cases(dataset)
    run_missing_media_case(dataset)
    run_forwarded_count_rule_check(dataset)
    run_pure_personal_case(dataset)
    run_batch_crash_isolation_case(dataset)

    print("\n" + "=" * 90)
    total, passed = len(_results), sum(_results)
    print(f"{passed}/{total} checks passed")
    print("=" * 90)
    if passed != total:
        sys.exit(1)
