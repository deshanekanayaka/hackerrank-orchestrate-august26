# 02: Approach (RFC-lite) — Routing Pipeline

Implements PRD FR-1 through FR-9. Written for Claude Code to build against directly.

## 1. Context and constraints

- Batch job, not a live service: read all of `dataset/`, produce `output.csv` in one run (re-runnable).
- Inputs: `messages.csv` (to route) plus 12 context files (users, groups, group_members, business_accounts, user_business_history, message_history, message_events, images, voice_notes, daily_notification_summary, sample_messages, output template).
- Media referenced by `images.csv` / `voice_notes.csv` must be opened from `dataset/media/` and actually inspected — metadata alone fails FR-5.
- 24-hour build window, unknown dataset row count until inspected — assume it could be large enough that naive "one LLM call per row with full history dumped in" is too slow/expensive; design for it from the start rather than retrofitting.
- No fine-tuning, no training-time access to hidden labels.

## 2. Design overview

Five-stage pipeline, each stage a separate, independently testable module:

```
messages.csv ─▶ [1. Context Assembly] ─▶ [2. Media Grounding] ─▶ [3. Decision Engine] ─▶ [4. Safety Override] ─▶ [5. Output Writer] ─▶ output.csv
                        │                        │                        │
                 users/groups/         OCR (images) / ASR       LLM, structured JSON
                 business/history      (voice) → text            output per schema
                 joined per row         appended to context
```

Stage 1 and 2 can run in parallel across rows (embarrassingly parallel, cache by `media_id`). Stage 3 is the expensive one — batch or parallelize with a concurrency cap to respect rate limits.

## 3. Component detail

### 3.1 Context Assembly
For each `message_id`, join in:
- Sender context: `sender_user_id` → nothing in `users.csv` describes the sender directly, but if sender is also a platform user, historical behavior toward this user may be inferable from `message_history.csv`.
- Receiver context: `users.csv` row (quiet hours, recent opens/replies/dismissals/reports).
- Group context (if `conversation_type == group`): `groups.csv` row + this user's `group_members.csv` row (role, mute state, read/reply pattern) — this is what makes FR-3 (muted group + urgent mention) possible.
- Business context (if `conversation_type == business`): `business_accounts.csv` row (verification, account age, reports, domain) + `user_business_history.csv` row for this user/business pair (orders, opt-in/opt-out).
- Evidence retrieval: pull candidate rows from `message_history.csv` filtered to (same `sender_user_id` OR same `group_id` OR same `business_id`) for this `user_id`, joined with `message_events.csv` for how the user reacted. Rank by recency + text/topic similarity to the current message; keep top-K (e.g. 5-10) as candidate evidence, not the full history — this bounds prompt size and directly produces well-grounded `evidence_message_ids` for FR-6.

### 3.2 Media Grounding
- Images (`media_type == image`): run OCR/vision extraction to get a text description + any embedded text (poster text, screenshot content). A vision-capable LLM call or a dedicated OCR lib both work; cache result keyed by `media_id` so repeated media isn't reprocessed.
- Voice notes (`media_type == voice`): run ASR to get a transcript. Cache keyed by `media_id`.
- Output of this stage is always plain text appended to the row's context, so Stage 3 only ever reasons over text — keeps the decision engine single-modal internally.

### 3.3 Decision Engine
- One structured-output call per message: input = message text/transcript/OCR text + assembled context + top-K evidence candidates; output = strict JSON matching `{action, message_type, reason, confidence, evidence_message_ids}` (validate/repair on parse failure, don't silently drop rows).
- Prompt should state the FR-3/FR-4 rules explicitly (mention-in-muted-group can still notify; safety overrides engagement) rather than relying on the model to infer them.
- Use `sample_messages.csv` purely as few-shot *style* calibration (reason tone/length, evidence format) in the prompt — not as instructions to match specific message_ids to specific outputs.

### 3.4 Safety Override (deterministic layer, applied after the LLM)
- Hard rule pass that can only tighten toward `mute`, never loosen away from it: e.g. business/sender with reports above a threshold in `business_accounts.csv`/`message_events.csv`, known scam-pattern text (payment links, urgency + credential requests), or a business the user has explicitly opted out of.
- This exists so FR-4 doesn't depend entirely on LLM judgment being right every time — deterministic rules are the backstop, LLM handles the nuanced personalization on top.
- Also validates `evidence_message_ids` here: drop any ID not actually present in `message_history.csv` (closes the hallucination risk from PRD §6) and fall back to `none` if the list becomes empty.

### 3.5 Output Writer
- Writes `output.csv` with the exact column order from the problem statement, one row per `message_id` in `messages.csv`, no extras, no missing rows.
- Run a final row-count and schema check against `messages.csv` and the blank `output.csv` template before calling it done.

## 4. Alternatives rejected

- **Pure rule-based system**: fast and cheap but can't produce the nuanced, per-user `reason` text or handle genuinely ambiguous cases (e.g. sale poster useful to one user, noise to another) — fails FR-2/FR-7 quality bar.
- **Pure LLM-only, full history in context, no retrieval**: simplest to build but doesn't bound prompt size, is slower/costlier at scale, and produces weaker evidence grounding (model picks IDs from a huge blob rather than a curated relevant set) — worse on FR-6.
- **Fine-tuning a classifier on sample_messages.csv**: 10ish labeled rows is nowhere near enough data, and the eval set is hidden — high overfitting risk, no time to validate.

## 5. Revisit triggers

- **Not triggered (2026-08-01 data audit)**: `messages.csv` is 110 rows (23 touching media). Per-row LLM calls at this scale don't need the hybrid split below — using a single model throughout for Stage 3. Revisit only if the row count grows materially before submission.
- ~~If `messages.csv` row count makes per-row LLM calls too slow for the 24h window even with concurrency: fall back to a cheaper/faster model for the bulk of rows and reserve the strongest model for rows flagged ambiguous (media present, conflicting signals, or business/scam borderline) by a cheap pre-classifier.~~
- If OCR/ASR quality is too noisy to trust: lower `confidence` proportionally rather than blocking the pipeline, and note it in `reason`.
- If rate limits are hit: add caching + retry-with-backoff at the media and decision stages rather than reducing coverage (every row still needs a prediction per FR-1).
