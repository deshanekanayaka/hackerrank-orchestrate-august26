# 01: Product Requirements — Message Notification Router

## 1. Problem

WhatsApp users receive a single undifferentiated stream mixing family chats, society/school notices, co-worker messages, business promotions, image posters, voice notes, and scams. Treating every message the same causes two failure modes: important messages get missed, and low-value/risky messages interrupt the user. The system must route each incoming message to `notify`, `digest`, or `mute`, personalized per user, and must reason over multimodal content (text, image, voice).

## 2. Users

- **Primary**: the WhatsApp end user receiving the routed notification. Their implicit job: "don't make me triage 200 messages a day, but never let me miss the one that matters or get scammed."
- **Secondary (evaluator)**: HackerRank's hidden ground-truth grader, scoring `output.csv` against held-out labels on action correctness, message_type correctness, reason quality, evidence relevance, and confidence calibration.

## 3. Functional requirements

Numbered so `02-approach.md` and `tasks.md` can cite them; do not renumber.

- **FR-1**: System reads every row of `dataset/messages.csv` and produces exactly one row per `message_id` in `output.csv`, with columns in the exact required order: `message_id, action, message_type, reason, confidence, evidence_message_ids`.
- **FR-2**: `action` is personalized per `user_id` using `users.csv`, `group_members.csv`, `user_business_history.csv`, and behavioral signals from `message_events.csv` — not a static rule keyed only on message content.
- **FR-3**: A muted group can still surface a `notify` if the message contains a direct/urgent mention to the receiving user (per group_members role/mention signal available in the data).
- **FR-4**: Clear scam or safety-risk content is muted regardless of the user's usual engagement pattern with that sender/group — safety overrides personalization, not the other way around.
- **FR-5**: Image messages are actually inspected (OCR/vision), not routed on filename or metadata alone; voice notes are actually transcribed (ASR), not routed on duration/metadata alone.
- **FR-6**: `evidence_message_ids` references real `message_id`s that exist in `message_history.csv` and are genuinely relevant (same sender, same group, or same topic/pattern) to the routing decision — never fabricated IDs, and `none` when no useful historical message exists.
- **FR-7**: `reason` is a short, specific, human-readable justification tied to the actual signals used (e.g. sender relationship, repetition pattern, media content, safety flag) — not a generic template restated per row.
- **FR-8**: `confidence` is calibrated: lower when signals conflict or media inspection is uncertain (e.g. noisy OCR/ASR), higher when multiple independent signals agree.
- **FR-9**: Solution is runnable end-to-end from the terminal, reads only from `dataset/`, uses environment variables for any API keys/secrets (never hardcoded), and does not use organizer-only files or hardcode labels for known rows.

## 4. Non-goals

- No model fine-tuning or training (no time budget in 24h; also risks overfitting to sample rows).
- No real WhatsApp integration — this is an offline batch scorer over the provided CSVs/media.
- No attempt to reverse-engineer the hidden ground-truth set; `sample_messages.csv` is for format/style calibration only, not for hardcoding predictions.
- No production-grade infra (queues, dashboards, retries-with-backoff-at-scale) — a clean, correct, single-run batch pipeline is the target.

## 5. Success metrics

Scored by HackerRank against hidden labels on:
1. `action` correctness
2. `message_type` correctness
3. usefulness/consistency of `reason`
4. relevance of `evidence_message_ids`
5. confidence calibration

Internal proxy metric before submission: accuracy against `dataset/sample_messages.csv` (the only rows with known-correct answers), used to sanity-check the pipeline — not to hand-tune per-row logic.

## 6. Risks

- **Media processing time/cost**: OCR + ASR across all image/voice rows may be slow or hit API rate limits within 24h → mitigate by caching per media_id and batching, and by choosing a fast OCR/ASR path early (see `02-approach.md`).
- **Evidence hallucination**: LLM inventing plausible-looking `message_id`s → mitigate with a post-hoc validation step that drops any evidence ID not present in `message_history.csv`.
- **Overfitting to sample_messages.csv**: writing rules that only work on the 5-10 solved examples → mitigate by keeping decision logic general (signals + reasoning), using samples only to check output *format* and *style*, not to special-case content.
- **Time overrun**: 24h is tight for multimodal + retrieval + LLM reasoning at scale → mitigate with the hour-by-hour checkpoints in `03-delivery-plan.md`, and a documented fallback (simpler heuristic-only path) if the full LLM pipeline isn't done by the hour-18 checkpoint.
