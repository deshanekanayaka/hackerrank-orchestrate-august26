"""Phase 4 validation: exercises pipeline.safety.apply_safety_overrides()
directly against real dataset rows -- no LLM call needed, since the whole
point of this module is that it behaves the same regardless of what the
LLM said. Feeds in a synthetic "generous" decision (high-confidence
notify, no evidence issues) for each case and checks the deterministic
layer still does the right thing.

Covers the two tasks.md Phase 4 checkpoints:
  - fake-high-engagement scam sender still gets muted
  - hallucinated evidence id gets filtered
plus a same-user-history evidence leak check, a real prior-report case,
a real business-domain-mismatch case, and a false-positive regression
check (a clean row must NOT get overridden).

Run: `python3 scripts/spotcheck_phase4.py` (from the code/ directory; no
OPENAI_API_KEY needed).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.data import load_all
from pipeline.safety import CONFIDENCE_CAP, apply_safety_overrides

PASS = "PASS"
FAIL = "FAIL"

_results = []


def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    _results.append(condition)
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))


def generous_decision(evidence_ids=None):
    return {
        "action": "notify",
        "message_type": "personal",
        "reason": "Sender is someone this user engages with often.",
        "confidence": 0.9,
        "evidence_message_ids": evidence_ids or [],
    }


def message_by_id(data, message_id: str) -> dict:
    row = data["messages"][data["messages"]["message_id"] == message_id]
    assert not row.empty, f"{message_id} not found in messages.csv"
    return row.iloc[0].to_dict()


def run_real_prior_report_case(data):
    print("=" * 90)
    print("Real case: receiving user previously reported this exact sender")
    print("=" * 90)
    # u_005 reported sender u_050 in group_005 (message_0016/0087/0088/... in
    # message_events.csv); msg_016 in messages.csv is a later message from
    # the same (user_id=u_005, sender_user_id=u_050, group_id=group_005) pair.
    message = message_by_id(data, "msg_016")
    decision = apply_safety_overrides(message, generous_decision(), data)
    check(
        "forced to mute despite generous LLM decision",
        decision["action"] == "mute",
        f"got action={decision['action']}",
    )
    check(
        "reason mentions the override",
        "safety override" in decision["reason"].lower(),
    )
    check(
        f"confidence capped at <= {CONFIDENCE_CAP}",
        decision["confidence"] <= CONFIDENCE_CAP,
        f"got confidence={decision['confidence']}",
    )


def run_real_domain_mismatch_case(data):
    print("\n" + "=" * 90)
    print("Real case: unverified business, sender domain != official domain")
    print("=" * 90)
    # business_064 (Talabat Refund Desk) is unverified with a mismatched,
    # freshly-registered domain -- msg_052 is a real message from it.
    message = message_by_id(data, "msg_052")
    decision = apply_safety_overrides(message, generous_decision(), data)
    check(
        "forced to mute despite generous LLM decision",
        decision["action"] == "mute",
        f"got action={decision['action']}",
    )


def run_fake_high_engagement_scam_case(data):
    print("\n" + "=" * 90)
    print("Checkpoint: scam-pattern sender muted even with faked high engagement")
    print("=" * 90)
    # Same business_064 case as above, but this time the LLM's own decision
    # explicitly cites strong (fabricated) engagement history as the reason
    # to notify -- the override must fire anyway, since it never looks at
    # the LLM's reasoning, only at business_accounts.csv's own fields.
    message = message_by_id(data, "msg_076")  # another real business_064 message
    fake_engaged_decision = {
        "action": "notify",
        "message_type": "business_update",
        "reason": (
            "User has opened every message from this business in the last "
            "180 days and replies within minutes -- clearly high engagement."
        ),
        "confidence": 0.95,
        "evidence_message_ids": [],
    }
    decision = apply_safety_overrides(message, fake_engaged_decision, data)
    check(
        "muted despite fabricated high-engagement justification",
        decision["action"] == "mute",
        f"got action={decision['action']}",
    )


def run_hallucinated_evidence_case(data):
    print("\n" + "=" * 90)
    print("Checkpoint: hallucinated evidence_message_id gets filtered")
    print("=" * 90)
    any_message = data["messages"].iloc[0].to_dict()
    user_id = any_message["user_id"]
    real_id = data["message_history"][data["message_history"]["user_id"] == user_id].iloc[0][
        "message_id"
    ]
    decision = apply_safety_overrides(
        any_message,
        generous_decision(evidence_ids=["message_9999_does_not_exist", real_id]),
        data,
    )
    check(
        "fabricated id dropped",
        "message_9999_does_not_exist" not in decision["evidence_message_ids"],
    )
    check(
        "real id for this user kept",
        real_id in decision["evidence_message_ids"],
    )
    check(
        f"confidence capped at <= {CONFIDENCE_CAP} once an id was dropped",
        decision["confidence"] <= CONFIDENCE_CAP,
        f"got confidence={decision['confidence']}",
    )


def run_evidence_leak_case(data):
    print("\n" + "=" * 90)
    print("Same-signature check: a real id belonging to a DIFFERENT user is also dropped")
    print("=" * 90)
    messages = data["messages"]
    user_a = messages.iloc[0]["user_id"]
    message_a = messages.iloc[0].to_dict()

    mhist = data["message_history"]
    other_user_row = mhist[mhist["user_id"] != user_a].iloc[0]
    foreign_id = other_user_row["message_id"]

    decision = apply_safety_overrides(
        message_a, generous_decision(evidence_ids=[foreign_id]), data
    )
    check(
        "a real but foreign-user id is dropped, not leaked",
        foreign_id not in decision["evidence_message_ids"],
    )


def run_no_false_positive_case(data):
    print("\n" + "=" * 90)
    print("Regression: a clean business message must NOT be overridden")
    print("=" * 90)
    # business_001 (Amazon India) is verified with a matching domain and a
    # report rate two orders of magnitude below the scam-pattern cluster.
    biz_messages = data["messages"][data["messages"]["business_id"] == "business_001"]
    if biz_messages.empty:
        print("(skipped -- no messages.csv row for business_001 in this dataset snapshot)")
        return
    message = biz_messages.iloc[0].to_dict()
    original = generous_decision()
    decision = apply_safety_overrides(message, dict(original), data)
    check(
        "action left untouched",
        decision["action"] == original["action"],
        f"got action={decision['action']}",
    )
    check(
        "confidence left untouched",
        decision["confidence"] == original["confidence"],
        f"got confidence={decision['confidence']}",
    )


if __name__ == "__main__":
    dataset = load_all()
    run_real_prior_report_case(dataset)
    run_real_domain_mismatch_case(dataset)
    run_fake_high_engagement_scam_case(dataset)
    run_hallucinated_evidence_case(dataset)
    run_evidence_leak_case(dataset)
    run_no_false_positive_case(dataset)

    print("\n" + "=" * 90)
    total, passed = len(_results), sum(_results)
    print(f"{passed}/{total} checks passed")
    print("=" * 90)
    if passed != total:
        sys.exit(1)
