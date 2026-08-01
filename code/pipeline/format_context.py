"""Formats one message row (real or from sample_messages.csv) plus its
assembled context, evidence candidates, and media grounding into a single
labeled text block for the Stage 3 prompt (FR-2 through FR-6).

Used for both real inference (messages.csv rows) and few-shot examples
(sample_messages.csv rows, see prompts.py) so the two are guaranteed to
look identical in shape -- one formatter, not two hand-maintained strings.
"""

from .context import assemble_context
from .evidence import retrieve_evidence
from .media import ground_message

SNIPPET_LEN = 140


def _snippet(text: str) -> str:
    return (text or "").strip().replace("\n", " ")[:SNIPPET_LEN]


def format_message_block(message: dict, data: dict) -> str:
    conversation_type = message["conversation_type"]
    ctx = assemble_context(message, data)
    evidence = retrieve_evidence(message, data)
    media = ground_message(message, data)

    lines = [
        f"Conversation type: {conversation_type}",
        f"Created at: {message.get('created_at', '')}",
        f"Forwarded count: {message.get('forwarded_count', '0')}",
    ]

    text = (message.get("message_text") or "").strip()
    lines.append(f"Message text: {text if text else '(empty)'}")

    media_type = (message.get("media_type") or "").strip()
    if media_type:
        if media["media_error"]:
            lines.append(
                f"Media ({media_type}): could not be processed -- {media['media_error']}. "
                "Treat media content as unknown, not absent; lower confidence accordingly."
            )
        else:
            media_text = media["media_text"] or ""
            lines.append(
                f"Media ({media_type}) transcript/OCR text (raw, possibly imperfect -- "
                f"see guardrail above): {media_text if media_text else '(no content extracted)'}"
            )

    receiver = ctx["receiver"]
    if receiver:
        lines.append(
            "Receiver: do_not_disturb_window={dnd}, messages_opened_30d={o}, "
            "messages_replied_30d={r}, notifications_dismissed_30d={d}, "
            "messages_reported_30d={rep}".format(
                dnd=receiver.get("do_not_disturb_window", ""),
                o=receiver.get("messages_opened_30d", ""),
                r=receiver.get("messages_replied_30d", ""),
                d=receiver.get("notifications_dismissed_30d", ""),
                rep=receiver.get("messages_reported_30d", ""),
            )
        )
    daily = ctx["receiver_daily_load"]
    if daily:
        recent = "; ".join(
            f"{d['date']}: sent={d['notifications_sent']} dismissed={d['notifications_dismissed']}"
            for d in daily
        )
        lines.append(f"Receiver recent notification load (last 7 days): {recent}")

    sender_user_id = (message.get("sender_user_id") or "").strip()

    if conversation_type == "group":
        group = ctx["group"]
        gm = ctx["group_membership"]
        if group:
            lines.append(
                "Group: name={n}, type={t}, member_count={m}, admin_count={a}, "
                "messages_30d={msg30}".format(
                    n=group.get("group_name", ""),
                    t=group.get("group_type", ""),
                    m=group.get("member_count", ""),
                    a=group.get("admin_count", ""),
                    msg30=group.get("messages_30d", ""),
                )
            )
        if gm:
            lines.append(
                "Receiver's membership: role={role}, group_muted_by_user={muted}, "
                "replies_sent_30d={rep}, notifications_dismissed_30d={dis}".format(
                    role=gm.get("role", ""),
                    muted=gm.get("group_muted_by_user", ""),
                    rep=gm.get("replies_sent_30d", ""),
                    dis=gm.get("notifications_dismissed_30d", ""),
                )
            )
        if sender_user_id:
            lines.append(f"Sender user_id: {sender_user_id}")

    if conversation_type == "business":
        business = ctx["business"]
        bh = ctx["business_history"]
        if business:
            lines.append(
                "Business: display_name={dn}, brand_name={bn}, category={c}, "
                "verified={v}, official_domain={od}, domain_used_by_sender={du}, "
                "account_age_days={age}, user_reports_30d={rep}, "
                "domain_used_by_sender_age_days={dua}".format(
                    dn=business.get("display_name", ""),
                    bn=business.get("brand_name", ""),
                    c=business.get("category", ""),
                    v=business.get("verified", ""),
                    od=business.get("official_domain", ""),
                    du=business.get("domain_used_by_sender", ""),
                    age=business.get("account_age_days", ""),
                    rep=business.get("user_reports_30d", ""),
                    dua=business.get("domain_used_by_sender_age_days", ""),
                )
            )
        if bh:
            lines.append(
                "Receiver's history with this business: why_user_knows_account={w}, "
                "allows_promotions={ap}, promotions_opted_out_at={oo}, "
                "activity_count_180d={ac}, messages_replied_30d={mr}".format(
                    w=bh.get("why_user_knows_account", ""),
                    ap=bh.get("allows_promotions", ""),
                    oo=bh.get("promotions_opted_out_at", ""),
                    ac=bh.get("activity_count_180d", ""),
                    mr=bh.get("messages_replied_30d", ""),
                )
            )

    if conversation_type == "personal" and sender_user_id:
        lines.append(f"Sender user_id: {sender_user_id}")

    if evidence:
        lines.append(
            "Candidate evidence messages (choose evidence_message_ids ONLY from "
            "these ids, or use an empty list if none are genuinely relevant):"
        )
        for c in evidence:
            lines.append(
                "  - id={mid} | when={ts} | snippet=\"{sn}\" | opened={op} "
                "replied={rp} dismissed={ds} reported={rep} muted_after={ma}".format(
                    mid=c["message_id"],
                    ts=c["created_at"],
                    sn=_snippet(c["message_text"]),
                    op=c.get("message_opened", ""),
                    rp=c.get("message_replied", ""),
                    ds=c.get("notification_dismissed", ""),
                    rep=c.get("message_reported", ""),
                    ma=c.get("muted_after_message", ""),
                )
            )
    else:
        lines.append("Candidate evidence messages: none available.")

    return "\n".join(lines)
