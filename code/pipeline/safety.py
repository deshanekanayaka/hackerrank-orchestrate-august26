"""Stage 4 safety override + evidence validation (FR-4, FR-6, FR-8).

Deterministic layer applied AFTER decide()/decide_all() and BEFORE the
output writer. Everything here can only tighten a decision toward `mute`
or lower `confidence` -- it never loosens an action or raises confidence.
The LLM stays the only source of nuanced personalization; this module is
just the backstop for the cases FR-4 says must never depend on the LLM
getting it right.

Three rules, each requiring a real, checkable field combination rather
than a single soft signal (see docs/tasks.md Phase 4 notes for the data
audit behind each threshold):

1. Prior report from this exact sender/business, by this exact receiving
   user (message_events.message_reported == 1 for a message_history row
   matching user_id + sender_user_id/business_id). A past report is a
   definitive user action, not a heuristic, so this alone is enough to
   force mute -- but it is still scoped to (this user, this sender), never
   globally, so it stays personalized per FR-2 instead of leaking across
   users.
2. Unverified business using a domain that doesn't match its own
   official_domain. Verified alone is common for small legitimate
   businesses and domain mismatch alone hits 2 verified accounts using
   link shorteners in this dataset -- neither is used in isolation.
   Combined, they land almost exactly on the 21 businesses in
   business_accounts.csv that were structurally built to look like
   phishing lookalikes (verified=0, mismatched young domain,
   user_reports_30d rate 1-9% vs a <0.7% ceiling for every verified
   business) -- see the 2026-08-01 data audit in this repo's tasks.md.
3. An explicit promotions opt-out (allows_promotions=0 or
   promotions_opted_out_at set) for a message the LLM itself classified
   as message_type=="promotion". This turns prompt rule 8's opt-out
   guidance into a guarantee instead of a suggestion.

Evidence validation drops any evidence_message_ids entry that isn't
actually in this receiving user's message_history.csv pool (the same
scope evidence.py retrieves from) -- closes both hallucination (invented
id) and leakage (a real id, but belonging to a different user) at once.

Confidence calibration is a single cap, not a stack of deductions: if
anything above fired, or media grounding failed for this message, or
validation had to drop an evidence id, confidence cannot exceed
CONFIDENCE_CAP. One rule, one number, easy to explain and test.
"""

from .media import ground_message

CONFIDENCE_CAP = 0.3


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes")


def _sender_previously_reported(message: dict, data: dict) -> bool:
    """True if THIS receiving user has already reported a past message from
    the same sender_user_id (personal/group) or business_id (business).

    Looks at the full message_history.csv pool for this user, not the
    top-K evidence.py candidates -- that list is capped and score-sorted,
    so a reported message could be truncated out of it even though it's
    exactly the kind of signal this rule must never miss.
    """
    user_id = message["user_id"]
    conversation_type = message["conversation_type"]
    sender_user_id = (message.get("sender_user_id") or "").strip()
    business_id = (message.get("business_id") or "").strip()

    mhist = data["message_history"]
    pool = mhist[mhist["user_id"] == user_id]
    if conversation_type == "business":
        if not business_id:
            return False
        pool = pool[pool["business_id"] == business_id]
    else:
        if not sender_user_id:
            return False
        pool = pool[pool["sender_user_id"] == sender_user_id]

    if pool.empty:
        return False

    mevents = data["message_events"]
    reported = mevents[(mevents["user_id"] == user_id) & (mevents["message_reported"] == "1")]
    if reported.empty:
        return False

    return bool(set(pool["message_id"]) & set(reported["message_id"]))


def _business_domain_mismatch(business: dict | None) -> bool:
    if not business:
        return False
    if _truthy(business.get("verified")):
        return False
    official = (business.get("official_domain") or "").strip()
    used = (business.get("domain_used_by_sender") or "").strip()
    return bool(official) and bool(used) and official != used


def _promotion_opt_out(business_history: dict | None, message_type: str) -> bool:
    if not business_history or message_type != "promotion":
        return False
    opted_out = not _truthy(business_history.get("allows_promotions", "1"))
    opted_out = opted_out or bool((business_history.get("promotions_opted_out_at") or "").strip())
    return opted_out


def apply_safety_overrides(message: dict, decision: dict, data: dict) -> dict:
    """Returns a new decision dict with FR-4/FR-6/FR-8 backstops applied.

    decision is the raw output of decide() -- {action, message_type, reason,
    confidence, evidence_message_ids} with evidence_message_ids as list[str].
    Re-derives context/business/media from message+data (all cheap: pandas
    filters and a cache lookup) rather than threading extra params through
    decide(), keeping decide() itself unchanged.
    """
    out = dict(decision)
    out["evidence_message_ids"] = list(decision.get("evidence_message_ids") or [])

    override_fired = False
    override_notes = []

    if out["action"] != "mute" and _sender_previously_reported(message, data):
        override_fired = True
        override_notes.append(
            "this user has previously reported a message from this same sender"
        )

    business = None
    business_history = None
    if message.get("conversation_type") == "business":
        business_id = (message.get("business_id") or "").strip()
        if business_id:
            rows = data["business_accounts"]
            match = rows[rows["business_id"] == business_id]
            business = match.iloc[0].to_dict() if not match.empty else None

            ubh = data["user_business_history"]
            bh_match = ubh[
                (ubh["user_id"] == message["user_id"]) & (ubh["business_id"] == business_id)
            ]
            business_history = bh_match.iloc[0].to_dict() if not bh_match.empty else None

    if out["action"] != "mute" and _business_domain_mismatch(business):
        override_fired = True
        override_notes.append(
            "sender domain does not match this business's verified official domain"
        )

    if out["action"] != "mute" and _promotion_opt_out(business_history, out.get("message_type")):
        override_fired = True
        override_notes.append("user has opted out of promotions from this business")

    if override_fired:
        out["action"] = "mute"
        out["reason"] = out.get("reason", "").rstrip(". ") + "; safety override: " + "; ".join(
            override_notes
        ) + "."

    valid_ids = set(data["message_history"][data["message_history"]["user_id"] == message["user_id"]][
        "message_id"
    ])
    original_ids = out["evidence_message_ids"]
    filtered_ids = [mid for mid in original_ids if mid in valid_ids]
    evidence_dropped = len(filtered_ids) != len(original_ids)
    out["evidence_message_ids"] = filtered_ids

    media = ground_message(message, data)
    media_uncertain = bool(media.get("media_error"))

    if override_fired or evidence_dropped or media_uncertain:
        try:
            confidence = float(out.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        out["confidence"] = min(confidence, CONFIDENCE_CAP)

    return out
