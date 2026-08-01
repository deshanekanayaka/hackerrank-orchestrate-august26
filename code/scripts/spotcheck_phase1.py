"""Phase 1 spot-check: context assembly + evidence retrieval.

Run from the code/ directory (or anywhere, paths are resolved relative to
this file): `python3 scripts/spotcheck_phase1.py`

Part A prints assembled context for a few representative rows (personal,
group, business, media) so it can be eyeballed by hand.

Part B runs evidence retrieval against every sample_messages.csv row that
has a known (non-'none') evidence_message_ids answer, and reports recall:
how many of the expected evidence message_ids show up in our top-K
candidates. sample_messages.csv rows are not present in messages.csv or
message_history.csv (separate, non-overlapping ID namespace -- see
docs/tasks.md Phase 0 notes) but their evidence_message_ids do reference
real message_history.csv rows, so this is a legitimate check against
known-correct answers.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.data import load_all
from pipeline.context import assemble_context
from pipeline.evidence import retrieve_evidence


def part_a_context_spotcheck(data, n=4):
    print("=" * 90)
    print("PART A: context assembly spot-check")
    print("=" * 90)
    messages = data["messages"]
    seen_types = set()
    picked = []
    for row in messages.to_dict("records"):
        ct = row["conversation_type"]
        if ct not in seen_types:
            seen_types.add(ct)
            picked.append(row)
        if row["media_type"] and not any(p.get("media_type") for p in picked[:-1]):
            if row not in picked:
                picked.append(row)
        if len(picked) >= n:
            break

    for row in picked:
        ctx = assemble_context(row, data)
        print(f"\n--- message_id={row['message_id']} user_id={row['user_id']} "
              f"conversation_type={row['conversation_type']} media_type={row['media_type'] or 'none'} ---")
        print("message_text:", (row["message_text"][:80] + "...") if len(row["message_text"]) > 80 else row["message_text"])
        print("receiver:", ctx["receiver"])
        print("receiver_daily_load (last 7):", len(ctx["receiver_daily_load"]), "rows")
        print("group:", ctx["group"])
        print("group_membership:", ctx["group_membership"])
        print("business:", ctx["business"])
        print("business_history:", ctx["business_history"])


def part_b_evidence_recall(data, top_k=10):
    print("\n" + "=" * 90)
    print(f"PART B: evidence retrieval recall against sample_messages.csv (top_k={top_k})")
    print("=" * 90)
    sample = data["sample_messages"]
    total_evaluated = 0
    total_expected = 0
    total_found = 0
    perfect = 0
    zero_hits = []

    for row in sample.to_dict("records"):
        expected_raw = row["evidence_message_ids"].strip()
        if not expected_raw or expected_raw.lower() == "none":
            continue
        expected_ids = {x.strip() for x in expected_raw.split(";") if x.strip()}
        candidates = retrieve_evidence(row, data, top_k=top_k)
        candidate_ids = {c["message_id"] for c in candidates}
        hits = expected_ids & candidate_ids

        total_evaluated += 1
        total_expected += len(expected_ids)
        total_found += len(hits)
        if hits == expected_ids:
            perfect += 1
        if not hits:
            zero_hits.append((row["message_id"], expected_ids, [c["message_id"] for c in candidates[:3]]))

        status = "OK  " if hits == expected_ids else ("PART" if hits else "MISS")
        print(f"[{status}] {row['message_id']}: expected={sorted(expected_ids)} "
              f"found={sorted(hits)} top3_candidates={[c['message_id'] for c in candidates[:3]]}")

    print(f"\nEvaluated {total_evaluated} sample rows with known evidence.")
    print(f"Recall: {total_found}/{total_expected} expected evidence IDs appeared in top-{top_k} candidates "
          f"({100*total_found/total_expected:.1f}%)" if total_expected else "no expected ids")
    print(f"Rows with full match: {perfect}/{total_evaluated}")
    if zero_hits:
        print("\nRows with zero overlap (inspect these):")
        for mid, exp, got in zero_hits:
            print(f"  {mid}: expected={sorted(exp)} got_top3={got}")


if __name__ == "__main__":
    data = load_all()
    part_a_context_spotcheck(data)
    part_b_evidence_recall(data)
