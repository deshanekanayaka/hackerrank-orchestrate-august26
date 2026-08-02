# Message Notification Router — runnable solution

Routes every message in `dataset/messages.csv` to `notify`, `digest`, or `mute`
and writes `dataset/output.csv`.

Architecture and design rationale live in [`../README.md`](../README.md) and
[`../docs/02-approach.md`](../docs/02-approach.md). This file is just how to run it.

---

## Setup

Requires **Python 3.11+** (developed and validated on 3.11.7).

```bash
cd code
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | **yes** | — | Used for the decision engine, image OCR, and voice-note transcription. |
| `DECISION_MODEL` | no | `gpt-4o-mini` | Stage 3 routing model. |
| `OCR_MODEL` | no | `gpt-4o-mini` | Vision model for image grounding. |
| `ASR_MODEL` | no | `whisper-1` | Transcription model for voice notes. |

Set the key either by exporting it or via a `.env` file at the repo root
(gitignored, loaded automatically):

```bash
export OPENAI_API_KEY=...
# or:  echo 'OPENAI_API_KEY=...' > ../.env
```

No secret is ever read from anywhere else — if `OPENAI_API_KEY` is unset the
pipeline fails immediately with a clear error rather than silently producing
fallback rows for all 110 messages.

---

## Run

```bash
cd code
python3 main.py
```

Writes `dataset/output.csv`: 110 rows, one per `messages.csv` row, in the same
order, with columns `message_id,action,message_type,reason,confidence,evidence_message_ids`.

**Expected runtime: ~2-4 minutes.** Dominated by the Stage 3 LLM calls (110
messages, 4 concurrent workers). The worker count is deliberately low — see
[Notes](#notes-worth-knowing) below.

The first run on a **cold media cache** is slower (~5-8 min): it OCRs 20 images
and transcribes 13 voice notes. `cache/media_cache.json` is committed, so this
normally doesn't happen — results are reused across runs and re-grading.

---

## Verify

Each phase of the build has a spot-check script. All are runnable standalone
from `code/`:

```bash
python3 scripts/spotcheck_phase8.py    # packaging + cache concurrency + output.csv validation
python3 scripts/spotcheck_phase5.py    # full-pipeline accuracy + structural validation
python3 scripts/spotcheck_phase4.py    # safety overrides + evidence validation (no API key needed)
python3 scripts/spotcheck_phase6.py    # robustness / edge cases
```

`spotcheck_phase4.py` and most of `spotcheck_phase8.py` are fully deterministic
and need **no API key**. Scripts that call the model for real say so and skip
cleanly when the key is absent.

To check just the graded artifact:

```bash
python3 scripts/spotcheck_phase8.py
```

This validates row count, column order, blank fields, confidence range, and
that every `evidence_message_ids` entry resolves to a real historical message
belonging to that row's user.

---

## Layout

```text
code/
├── main.py                    # entry point: load -> decide -> safety -> write CSV
├── requirements.txt
├── README.md                  # you are here
├── cache/media_cache.json     # committed OCR/ASR results, keyed by media_id
├── pipeline/
│   ├── data.py                # loads all dataset CSVs (everything as strings)
│   ├── context.py             # Stage 1: per-message user/group/business context
│   ├── evidence.py            # Stage 1: top-K historical evidence retrieval
│   ├── media.py               # Stage 2: image OCR + voice-note ASR, cached
│   ├── format_context.py      # renders the context block shown to the model
│   ├── prompts.py             # Stage 3: routing rules + fixed few-shot set
│   ├── decision.py            # Stage 3: structured-output LLM call
│   └── safety.py              # Stage 4: deterministic overrides + evidence validation
└── scripts/                   # per-phase validation, see Verify above
```

---

## Notes worth knowing

- **Determinism.** `temperature=0` and a strict `json_schema` response format
  constrain `action`/`message_type` to valid values at the API level, but
  `gpt-4o-mini` is still not perfectly deterministic — action accuracy on the
  30 solved sample rows varies between 86.7% and 90.0% run to run. A rerun may
  produce slightly different `output.csv` content.
- **Worker count.** `decide_all()` defaults to 4 workers. It was 6, until a
  real full run put 19 of 110 rows over the account's TPM cap and into retry
  exhaustion. If you hit 429s, lower it further rather than raising it.
- **Media cache staleness.** Entries are keyed by `media_id` and a cache
  version, not a content hash. Replacing a media file under the same ID serves
  the old result; delete that entry by hand if it happens.
- **Model pinning.** The defaults are floating aliases, not dated snapshots.
  A provider-side repoint changes behavior with no change on this end — pin
  explicit snapshot IDs via the env vars above if you need run-to-run stability
  over a long period.

For what would change if this were deployed for real — extension seams,
scaling, and the failure modes this design would hit in production — see
[`../docs/production-considerations.md`](../docs/production-considerations.md).
