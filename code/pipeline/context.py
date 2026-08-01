"""Context assembly: message -> receiver/group/business context (FR-2).

assemble_context() joins one row of messages.csv (or message_history.csv, or
sample_messages.csv) against users/groups/group_members/business_accounts/
user_business_history/daily_notification_summary and returns a plain dict.
Missing rows (e.g. no group_members row for a personal message) resolve to
None rather than raising, since conversation_type determines which joins
even apply.
"""

from typing import Any


def _row_to_dict(df, mask) -> dict[str, Any] | None:
    matches = df[mask]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()


def assemble_context(message: dict, data: dict) -> dict:
    user_id = message["user_id"]
    conversation_type = message["conversation_type"]
    group_id = message.get("group_id", "")
    business_id = message.get("business_id", "")

    receiver = _row_to_dict(data["users"], data["users"]["user_id"] == user_id)

    recent_daily = data["daily_notification_summary"]
    recent_daily = recent_daily[recent_daily["user_id"] == user_id].sort_values("date")
    daily_load = recent_daily.tail(7).to_dict("records")

    group = None
    group_membership = None
    if conversation_type == "group" and group_id:
        group = _row_to_dict(data["groups"], data["groups"]["group_id"] == group_id)
        gm = data["group_members"]
        group_membership = _row_to_dict(
            gm, (gm["group_id"] == group_id) & (gm["user_id"] == user_id)
        )

    business = None
    business_history = None
    if conversation_type == "business" and business_id:
        business = _row_to_dict(
            data["business_accounts"], data["business_accounts"]["business_id"] == business_id
        )
        ubh = data["user_business_history"]
        business_history = _row_to_dict(
            ubh, (ubh["user_id"] == user_id) & (ubh["business_id"] == business_id)
        )

    return {
        "receiver": receiver,
        "receiver_daily_load": daily_load,
        "group": group,
        "group_membership": group_membership,
        "business": business,
        "business_history": business_history,
    }
