# AdProof — Phase 1 vertical slice

Proves one thing end to end: **a real video can be ingested into VideoDB,
indexed for speech and vision, searched for real timestamped evidence,
deterministically evaluated, and reviewed by opening the real video at the
retrieved moment** — with processing, empty-result, and failure states all
represented honestly.

Provider behaviour was verified before implementation. See
[`../docs/VIDEODB_VERIFIED_BEHAVIOR.md`](../docs/VIDEODB_VERIFIED_BEHAVIOR.md).

## What is deliberately not here

Absent, not stubbed: authentication, authorization, workspace isolation
enforcement, rule editor, brief-to-rule extraction, review actions
(confirm/override/escalate), approval decisions, evidence reels, exports,
analytics, webhooks. Only two rule types exist. `/api/integrity` states these
limits, and the UI renders them.

There is **no fixture data anywhere in this build.** `EvidenceOrigin` has a
single member, `live_provider`, so fixture evidence is not representable. If
VideoDB is unavailable, processing fails visibly and no report is produced.

## Setup

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
createdb adproof
cp .env.example .env      # then set VIDEODB_API_KEY
```

## Verify the provider first (recommended)

```bash
VIDEODB_API_KEY=... ./.venv/bin/python scripts/verify_videodb_spike.py \
  "https://example.com/your-video.mp4" "a phrase in the audio" "something visible"
```

Prints the real provider responses and resolves the open assumptions listed in
`VIDEODB_VERIFIED_BEHAVIOR.md` s4. It writes nothing to the database.

## Run

Two processes. The worker is separate because provider calls block.

```bash
# terminal 1 — API + UI
VIDEODB_API_KEY=... ./.venv/bin/uvicorn adproof.api.main:app --reload

# terminal 2 — worker
VIDEODB_API_KEY=... ./.venv/bin/python -m adproof.orchestrator.worker

# terminal 3 — seed one campaign + submission
./.venv/bin/python scripts/seed_slice.py "https://example.com/video.mp4" \
    --phrase AYUSH20 --concept "PulseBar protein bar package" --seconds 6
```

Open <http://127.0.0.1:8000/> and select the submission.

Expect ingestion and indexing to take minutes. Visual indexing runs a VLM over
a frame every 2 seconds, so cost scales with video length — start short.

## Tests

```bash
createdb adproof_test
./.venv/bin/python -m pytest
```

Postgres-backed tests skip automatically if no database is reachable.

| File | Covers |
| --- | --- |
| `test_intervals.py` | merge, dedupe, clamping, idempotency, no double counting |
| `test_evaluators.py` | pass/fail paths, every absence class, every absence policy |
| `test_integrity.py` | no fixture fallback, no invented SDK usage, measurement purity, mandatory provenance, forbidden UI language |
| `test_persistence.py` | idempotency and DB-enforced immutability |
| `test_api.py` | contracts, plus a provider failure driven through the real worker |

## Architecture

```
api/main.py         HTTP + integrity disclosure + static UI
orchestrator/       durable job queue; blocking provider calls run here
  jobs.py           claim/succeed/fail, bounded retries, dedupe
  steps.py          ingest → index → retrieve → evaluate
  worker.py         loop; one transaction per job
providers/          the ONLY module importing the VideoDB SDK
retrieval/plan.py   rule → versioned, reproducible searches
evaluation/         PURE. intervals, absence, confidence, evaluators
models.py           schema; immutability enforced by DB triggers
```

Pipeline stages are chained forward only after genuine success, so no stage can
appear to start on the strength of a stage that failed.

### Where the integrity guarantees live

| Guarantee | Enforced by |
| --- | --- |
| No fixture evidence | `EvidenceOrigin` has one member; `test_integrity.py` |
| No silent fallback | Adapter never returns data from an `except` block; asserted by AST test |
| No invented SDK usage | Every method and constant asserted against the pinned SDK |
| Deterministic measurement | `evaluation/` is pure; AST test forbids network/model identifiers |
| Absence ≠ failure | `absence.py`; a semantic miss can never fail |
| Provenance on all evidence | Provenance columns are `NOT NULL` |
| Immutable audit artifacts | Postgres triggers, not application convention |
| Idempotency | Unique constraints on `dedupe_key` and `idempotency_key` |

## Known gaps in this build

1. **No access control on playback.** `/api/evidence/{id}/playback` returns a
   provider URL that is assumed not to be access controlled. Do not use with
   real creator media.
2. **No authentication.** Every caller sees every workspace.
3. **Coarse async state.** No progress percentage, because VideoDB gives this
   SDK version no progress signal.
4. **Uncalibrated confidence.** Bands are provisional and labelled as such.
5. **Re-evaluation not supported.** Results are immutable and a second
   evaluation is skipped rather than overwriting history; report versioning is
   Phase 4 work.
