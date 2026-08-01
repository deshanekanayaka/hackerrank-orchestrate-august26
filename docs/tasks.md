# Tasks — Message Notification Router

Live checklist for Claude Code across sessions during the 24h window. Check items as done; if a session resets, read this file first to see where the build actually is before touching `01-PRD.md` / `02-approach.md` again. Reference: `01-PRD.md` (FR numbers), `02-approach.md` (stage numbers), `03-delivery-plan.md` (hour checkpoints).

## Phase 0 — Setup & data reality check
- [x] Confirm runtime/environment set up, dependencies installable — Python 3.11.7 + pip3 + node 24.10.0 all available; no project deps pinned yet (none needed until Phase 1/2 library choices are made)
- [x] Row counts for `messages.csv` and every context file logged somewhere (comment or notes file) — see Notes below
- [x] Media type distribution (image/voice/none) logged — see Notes below
- [x] `sample_messages.csv` read closely; style notes taken for `reason` tone and `evidence_message_ids` format — see Notes below
- [x] Decision made: single-model pipeline, or cheap+expensive hybrid (only if row count is large) — single-model; see Notes below

## Phase 1 — Context assembly + evidence retrieval
- [x] Join function: message_id → sender/receiver/group/business context (FR-2) — `code/pipeline/context.py::assemble_context`
- [x] Evidence retrieval function: filtered, ranked, top-K candidates from message_history.csv + message_events.csv (FR-6) — `code/pipeline/evidence.py::retrieve_evidence`
- [x] Spot-checked against 3-4 sample rows by hand — `code/scripts/spotcheck_phase1.py`; context assembly eyeballed on 4 rows (business/personal/group/group+image), evidence retrieval scored against all 28 sample_messages.csv rows with known evidence

## Phase 2 — Media grounding
- [ ] OCR path for images working, cached by media_id (FR-5)
- [ ] ASR path for voice notes working, cached by media_id (FR-5)
- [ ] Spot-checked on real files from dataset/media/

## Phase 3 — Decision engine
- [ ] Prompt written covering FR-2 through FR-8 explicitly (muted-group-mention rule, safety-overrides-engagement rule)
- [ ] Structured JSON output wired up with parse validation/retry
- [ ] Run against sample_messages.csv, action/message_type compared to expected
- [ ] Edge cases don't crash it (empty text, missing group_id, missing media file)

## Phase 4 — Safety override + evidence validation
- [ ] Deterministic mute-tightening rules implemented (FR-4)
- [ ] Evidence ID validation implemented — hallucinated IDs dropped, falls back to `none` (FR-6)
- [ ] Confidence calibration adjustment implemented (FR-8)
- [ ] Tested: fake-high-engagement scam sender still gets muted
- [ ] Tested: hallucinated evidence ID gets filtered

## Phase 5 — Full run + evaluation
- [ ] Full pipeline run over all of messages.csv
- [ ] output.csv produced with correct row count and column order (FR-1)
- [ ] Accuracy check against sample_messages.csv
- [ ] ~15-20 non-sample rows spot-checked by hand for plausibility

## Phase 6 — Robustness pass
- [ ] Empty message_text handled
- [ ] Missing media file (referenced but absent) handled without crash
- [ ] forwarded_count signal actually used somewhere in reasoning
- [ ] Pure personal conversation_type (no group/business context) handled
- [ ] One bad row doesn't crash the batch; every message_id still gets a row

## Phase 7 — Polish
- [ ] reason strings reviewed for genericness across a broad sample (FR-7)
- [ ] No hardcoded secrets anywhere; API keys from env vars only (FR-9)
- [ ] No organizer-only files or hidden-label shortcuts used (FR-9)

## Phase 8 — Packaging
- [ ] Code README written (setup, run instructions, env vars, expected runtime)
- [ ] code.zip assembled (solution + prompts/configs + README + eval scripts)
- [ ] Final output.csv validated: row count, columns, no blank required fields
- [ ] AGENTS.md transcript log path populated (if using a compatible tool)

## Phase 9 — Buffer & submit
- [ ] Final full run clean
- [ ] code.zip, output.csv, chat transcript ready per repo's submission instructions

## Notes / open questions
(Add anything that came up during the build that later phases need to know — e.g. "row count is X, went with hybrid model approach" or "OCR library Y chosen because Z".)

- **2026-08-01 data audit (full report given, dataset confirmed clean)**: `messages.csv` = 110 rows to route (87 text-only, 15 image, 8 voice). Context files: `users.csv` 54, `groups.csv` 23, `group_members.csv` 401, `business_accounts.csv` 110, `user_business_history.csv` 106, `message_history.csv` 412, `message_events.csv` 412, `images.csv` 20, `voice_notes.csv` 13, `daily_notification_summary.csv` 756, `sample_messages.csv` 30. No nulls/dupes/encoding/ragged-row issues; all foreign keys resolve; all referenced media files exist on disk; `output.csv` template columns/order/row-set match `messages.csv` exactly. `wc -l` undercounts `messages.csv`/`message_history.csv` because `message_text` has embedded newlines inside quotes — use a real CSV parser, not line counts.
- **ID namespace mismatch — do not join on message_id string format**: `messages.csv` uses `msg_023` style IDs, `message_history.csv` uses `message_0107` style IDs (different prefix and zero-padding), and `sample_messages.csv` uses its own `sample_msg_001` style IDs with zero overlap with either real file. Evidence retrieval (FR-6) must join `message_history.csv` candidates via `sender_user_id`/`group_id`/`business_id`/`user_id` foreign keys, never by parsing or pattern-matching the `message_id` string itself.
- **Decision: single-model pipeline, no cheap/expensive hybrid.** Row count is 110 (23 of which touch media) — small enough that the hybrid fallback in `02-approach.md` §5 (cheap pre-classifier routing to a stronger model only for ambiguous rows) isn't needed for the 24h window. Revisit only if `messages.csv` changes size significantly before submission.
- **Phase 1 built and validated (2026-08-01)**: `code/pipeline/data.py` loads all dataset CSVs as strings (avoids pandas mangling ID columns). `code/pipeline/context.py` joins a message row to receiver (users.csv + last 7 days of daily_notification_summary.csv), group (groups.csv + group_members.csv), and business (business_accounts.csv + user_business_history.csv) context, whichever applies per conversation_type. `code/pipeline/evidence.py` retrieves top-K message_history.csv candidates for the same user_id, joined with message_events.csv.
- **Evidence retrieval design note — relation match is a scoring signal, not a hard filter.** Initial version hard-filtered to same sender_user_id/group_id/business_id only, which missed 3/28 sample rows where the expected evidence was from a *different* channel but the same topic/pattern (e.g. a business "shopping offer" message evidenced by an unrelated group's "cashback reminder" — legitimate per FR-6's "same sender, same group, or same topic/pattern" wording). Fixed by scoring all same-user history (`score = 0.40*relation + 0.25*recency + 0.35*text_similarity`, `SequenceMatcher` ratio for text_sim) and only hard-excluding candidates that are both unrelated by channel AND below a 0.15 score floor. `top_k=10` chosen (approach doc's suggested 5-10 range) after comparing recall at 8/10/12.
- **Evidence retrieval validated against sample_messages.csv**: 27/28 sample rows with known evidence get a full match (all expected message_ids present in top-10); 30/31 individual expected IDs recovered (96.8% recall). The one remaining miss (`sample_msg_042`) is a voice-note message with no `message_text` yet — text similarity can't work until Phase 2 (ASR) fills in a transcript; expected to resolve then, not a Phase 1 defect. Re-run via `python3 code/scripts/spotcheck_phase1.py`.
