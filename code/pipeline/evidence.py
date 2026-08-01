"""Evidence retrieval: message -> top-K relevant message_history candidates (FR-6).

Pool is every message_history.csv row for the same receiving user_id. FR-6
allows evidence to be "same sender, same group, or same topic/pattern" --
so relation match (same sender_user_id/group_id/business_id as the current
message) is a strong *scoring* signal, not a hard filter: a topically similar
message from a different sender/group/business can still qualify (e.g. a
scam pattern recurring across different business accounts). A minimum score
floor keeps genuinely unrelated history out of the results.

message_id namespaces differ across files (messages.csv uses msg_XXX,
message_history.csv uses message_XXXX) -- this module never parses or
compares message_id strings; all joins go through the foreign keys
(user_id, sender_user_id, group_id, business_id).
"""

from datetime import datetime
from difflib import SequenceMatcher

TS_FORMAT = "%Y-%m-%d %H:%M"


def _parse_ts(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, TS_FORMAT)
    except ValueError:
        return None


def _text_sim(a: str, b: str) -> float:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


MIN_SCORE = 0.15


def retrieve_evidence(message: dict, data: dict, top_k: int = 10) -> list[dict]:
    user_id = message["user_id"]
    conversation_type = message["conversation_type"]
    group_id = (message.get("group_id") or "").strip()
    business_id = (message.get("business_id") or "").strip()
    sender_user_id = (message.get("sender_user_id") or "").strip()
    message_text = message.get("message_text", "")
    message_ts = _parse_ts(message.get("created_at", ""))

    mhist = data["message_history"]
    pool = mhist[mhist["user_id"] == user_id]

    def relation_score(row) -> float:
        if conversation_type == "business":
            return 1.0 if row["business_id"] == business_id and business_id else 0.0
        if conversation_type == "personal":
            return 1.0 if row["sender_user_id"] == sender_user_id and sender_user_id else 0.0
        if conversation_type == "group":
            same_sender = bool(sender_user_id) and row["sender_user_id"] == sender_user_id
            same_group = bool(group_id) and row["group_id"] == group_id
            if same_sender and same_group:
                return 1.0
            if same_sender:
                return 0.9
            if same_group:
                return 0.7
            return 0.0
        return 0.0

    events_by_key = {
        (r["user_id"], r["message_id"]): r for r in data["message_events"].to_dict("records")
    }

    candidates = []
    for row in pool.to_dict("records"):
        rel = relation_score(row)
        cand_ts = _parse_ts(row.get("created_at", ""))
        if message_ts and cand_ts:
            day_diff = max((message_ts - cand_ts).days, 0)
            recency = 1.0 / (1.0 + day_diff / 30.0)
        else:
            recency = 0.0
        sim = _text_sim(message_text, row.get("message_text", ""))
        score = 0.40 * rel + 0.25 * recency + 0.35 * sim

        # Same-channel history (rel > 0) is kept regardless of score since FR-6
        # treats "same sender/group/business" as inherently relevant; anything
        # else needs to clear MIN_SCORE on recency+topic similarity alone.
        if rel <= 0.0 and score < MIN_SCORE:
            continue

        event = events_by_key.get((user_id, row["message_id"]), {})
        candidates.append(
            {
                "message_id": row["message_id"],
                "created_at": row.get("created_at", ""),
                "message_text": row.get("message_text", ""),
                "media_type": row.get("media_type", ""),
                "sender_user_id": row.get("sender_user_id", ""),
                "group_id": row.get("group_id", ""),
                "business_id": row.get("business_id", ""),
                "relation_score": rel,
                "recency_score": round(recency, 4),
                "text_similarity": round(sim, 4),
                "score": round(score, 4),
                "message_opened": event.get("message_opened", ""),
                "message_replied": event.get("message_replied", ""),
                "notification_dismissed": event.get("notification_dismissed", ""),
                "muted_after_message": event.get("muted_after_message", ""),
                "message_reported": event.get("message_reported", ""),
            }
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:top_k]
