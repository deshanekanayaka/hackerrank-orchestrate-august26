# 03: Delivery Plan — 24-Hour Window

Checkpoints, not a rigid schedule — if a stage finishes early, pull the next one forward. If a checkpoint is missed by more than ~1.5h, invoke the fallback noted at that checkpoint rather than letting the whole plan slip.

## Phase 0 — Setup & data reality check (Hour 0–1)

- Confirm repo runs: check `AGENTS.md` / existing `CLAUDE.md` for any environment setup already specified; set up the language/runtime (Python recommended for OCR/ASR + CSV ergonomics, but JS/TS fine per repo rules).
- Inspect every file in `dataset/`: row counts, `messages.csv` size, how many rows have `media_type == image` vs `voice` vs empty, distribution of `conversation_type`.
- Read `sample_messages.csv` closely — this is the only ground truth available; note the tone/length/style of `reason`, and the format used for `evidence_message_ids`.
- **Checkpoint**: know exact row counts and media counts. If `messages.csv` is large (order of thousands+) or media count is high, flag now that Phase 3 needs the cheaper-model fallback from `02-approach.md` §5 — decide this early, not at hour 14.

## Phase 1 — Context assembly + evidence retrieval (Hour 1–3)

- Build the join layer: per `message_id`, assemble sender/receiver/group/business context.
- Build the evidence retrieval function (filter `message_history.csv` by sender/group/business + this user, joined with `message_events.csv`, ranked, top-K).
- Unit-test this against 3-4 rows from `sample_messages.csv` by hand — does the retrieved evidence look like what a human would cite?
- **Checkpoint**: can produce a fully assembled context blob (text) for any given `message_id`.

## Phase 2 — Media grounding (Hour 3–6)

- Implement OCR path for images, ASR path for voice notes. Cache both by `media_id`.
- Spot check output quality on a handful of real media files from `dataset/media/`.
- **Checkpoint**: every image/voice row can be turned into plain text. **Fallback**: if ASR/OCR setup is eating too much time, use a vision/audio-capable LLM call directly instead of a dedicated library — slower per-call but far less setup.

## Phase 3 — Decision engine (Hour 6–11)

- Write the structured-output prompt (system instructions covering FR-2 through FR-8, few-shot style from `sample_messages.csv`).
- Wire it to context assembly + media grounding output.
- Run against `sample_messages.csv` rows first; compare predicted vs expected `action`/`message_type`, iterate on the prompt.
- Add JSON parse validation + repair-retry on malformed output.
- **Checkpoint**: predictions on the sample set look directionally right (not necessarily perfect) and the pipeline runs without crashing on edge cases (empty text, missing media file, missing group_id).

## Phase 4 — Safety override + evidence validation (Hour 11–13)

- Implement the deterministic mute-tightening rules (scam/report thresholds, opt-out honoring).
- Implement evidence ID validation (drop hallucinated IDs, fall back to `none`).
- Implement confidence calibration adjustment (lower on noisy media, conflicting signals).
- **Checkpoint**: a deliberately-scam-like sample row is muted even if you fake "high engagement" history for that sender; a hallucinated evidence ID gets filtered out in a test.

## Phase 5 — Full run + evaluation (Hour 13–16)

- Run the full pipeline over all of `messages.csv`, writing `output.csv`.
- Evaluate against `sample_messages.csv` (the only labeled subset) for a sanity accuracy read.
- Spot-check a random sample of ~15-20 non-sample rows by hand for plausibility (not just "did it run").
- **Checkpoint**: `output.csv` exists, has one row per `message_id`, correct columns/order, and sample-row accuracy is acceptable. **Fallback**: if accuracy is poor, don't start over — diagnose which stage is weakest (retrieval? media? prompt?) and fix that stage only.

## Phase 6 — Robustness pass (Hour 16–19)

- Handle edge cases explicitly: empty `message_text`, missing `media_id` referenced but file absent, `forwarded_count` signal, rows with no group/business context at all (pure `personal` conversation_type).
- Add basic error handling so one bad row doesn't crash the whole batch — log and continue, but every `message_id` must still get a row (FR-1 is non-negotiable).
- Re-run full pipeline; diff against the Phase 5 output to make sure nothing regressed.

## Phase 7 — Polish (Hour 19–21)

- Review `reason` strings across a broad sample — cut generic/templated language, make sure each is tied to real signals used (FR-7).
- Confirm no hardcoded secrets; confirm API keys are read from environment variables only.
- Confirm no organizer-only files or hidden-label shortcuts were used anywhere in the code path.

## Phase 8 — Packaging (Hour 21–23)

- Write the code README: setup instructions, how to run, environment variables needed, expected runtime.
- Assemble `code.zip`: full runnable solution, prompts/configs, README, any evaluation scripts used in Phase 5.
- Final `output.csv` validation: row count matches `messages.csv`, columns match spec exactly, no blank required fields.
- Confirm the `AGENTS.md`-specified transcript log path (`$HOME/hackerrank_orchestrate_august26/log.txt` or Windows equivalent) is actually being populated if using a compatible AI coding tool — needed for submission.

## Phase 9 — Buffer & submit (Hour 23–24)

- No new features. Only: fix anything broken by the final full run, re-zip if needed, submit code.zip + output.csv + chat transcript per the repo's submission instructions.
