# AdProof

**Creator campaign verification with timestamped evidence.**

Brands and agencies pay creators based on whether a video followed the brief.
Today that decision is made by someone watching the video with a checklist in
their head. When a creator disputes a rejection, or a brand disputes a payout,
there is no artifact to point at.

AdProof turns campaign requirements into structured rules, checks a submitted
video against them using VideoDB, and returns the exact moment behind every
result. Reviewers confirm or override, and the whole chain is recorded.

Built for the VideoDB Hack Day, 25-27 July 2026. Track: **compliance review**.

---

## What it does

```
requirement  ->  media  ->  evidence  ->  measurement  ->  human decision  ->  record
```

1. **Author requirements.** Nine rule types: required phrase, prohibited claim,
   minimum/maximum visual duration, required/forbidden visual event, disclosure,
   sequence, and subjective. A named person confirms the rule set, and it is
   immutable from then on.
2. **Ingest and index.** The video goes into VideoDB. A spoken-word index plus
   up to five *focused* visual indexes are built, one per domain.
3. **Retrieve.** Each rule runs versioned searches for supporting **and**
   conflicting moments.
4. **Measure deterministically.** Durations, counts, ordering and thresholds are
   computed in application code, never by a language model.
5. **Adjudicate.** A machine recommendation, plus a gate: approval is
   unavailable until every requirement has passed or been reviewed.
6. **Review.** Open any finding at its exact timestamp. Confirm or override with
   a recorded reason. The machine result is never overwritten.

## The part that matters

Most of the engineering here defends one claim: **a search miss is not proof of
absence.**

When nothing is found, AdProof records *why*: `likely_absent`,
`low_confidence_absence`, `index_incomplete`, `query_insufficient`,
`media_quality_issue`, or `provider_failure`. Only the strongest of those can
produce a failure, and only when the rule explicitly opts in. A semantic visual
miss can never fail a rule, whatever the policy says.

Related guarantees, all enforced rather than documented:

| Guarantee | How |
| --- | --- |
| No fabricated evidence | `EvidenceOrigin` has exactly one member, `live_provider`. Fixture evidence is not representable. |
| No silent fallback | The adapter never returns data from an `except` block. Asserted by an AST test. |
| No invented SDK usage | Every VideoDB method and constant used is asserted against the pinned SDK at test time. |
| Deterministic measurement | `evaluation/` is pure. An AST test forbids network and model identifiers in it. |
| Immutable audit artifacts | Postgres triggers, not application convention. |
| Honest uncertainty | A measurement within one sampling interval of its threshold routes to a human instead of picking a side. |

## How a rule is decided

Three layers, kept separate on purpose, each recorded separately in the result:

1. **Retrieval** — VideoDB returns timestamped descriptions with provider
   scores. Nothing is concluded here.
2. **Deterministic measurement** — pure Python merges intervals, totals
   durations, counts occurrences, and compares against the threshold. No model
   touches a number.
3. **Reading** — a language model receives the requirement, the deterministic
   measurement *as a stated fact*, and both the supporting and the conflicting
   descriptions, then returns its own verdict and reasoning.

Both conclusions are stored. Where they disagree, the report says so, shows
each side, and drops confidence to `low` — because two systems reaching
different answers is information a reviewer needs, not noise to average away.

This layer exists because of a real failure: a rule requiring the creator to
*use* the product measured 36.8 seconds of screen time and passed, while the
descriptions underneath it literally read "no one is visibly interacting with
the product". Arithmetic cannot see that. Reading can.

Guardrails:

- Rules whose answer is arithmetic (exact spoken-phrase counts) never reach a
  model at all.
- An unparseable or off-vocabulary answer becomes `uncertain`, never `pass`.
- A rule the evaluator escalated for human review cannot be cleared to `pass`
  by a model re-reading the same descriptions.
- If no model is reachable, the deterministic result stands and the report says
  no reading happened. There is no canned fallback verdict.

Provider: OpenRouter free tier when `OPENROUTER_API_KEY` is set (a chain of
free models, since free slugs get retired mid-flight — `llama-3.3-70b-instruct:free`
started returning 404 "no longer free" during development), otherwise VideoDB's
own `Collection.generate_text`. Whichever answered is named in the result.

## VideoDB primitives used

Pinned to `videodb==0.5.1`. Everything below was verified by source
introspection and then against the live API.

| Primitive | Where | Purpose |
| --- | --- | --- |
| `videodb.connect()` | `providers/videodb_adapter.py` | Server-side only; the key never reaches a browser |
| `Connection.get_collection()` | adapter | One collection per workspace |
| `Collection.upload(url=/file_path=)` | ingest job | Media ingestion |
| `Video.index_spoken_words(language_code=)` | index job | Spoken-word index |
| `Video.index_scenes(extraction_type=time_based, extraction_config={time, frame_count, select_frames}, prompt=, name=)` | index job | One **focused** visual index per domain |
| `Video.list_scene_index()` | readiness gate | Real provider `status` per scene index |
| `Video.get_scene_index(id)` | readiness gate | Confirms records exist before searching |
| `Video.legacy_search(query, search_type, index_type, score_threshold, result_threshold, scene_index_id)` | retrieval | Per-rule reproducible search, keyword and semantic |
| `Video.generate_stream()` | playback | HLS stream, proxied by AdProof |
| `Video.delete_scene_index(id)` | cleanup | Removing duplicate indexes |
| `Collection.generate_text(prompt, model_name="pro")` | evidence qualification, rule verdict | Reading retrieved descriptions — never measuring |

**Deliberately not used:** `ask()` and Search V2's high-level `search()`. `ask`
is a chat-over-video affordance and falls outside this product's boundary;
`legacy_search` is what lets each rule name its exact index and search mode,
which is what makes a result reproducible.

### Five findings from live verification

Verifying against the real API rather than the docs caught five bugs. Full
detail with observed values in
[`docs/VIDEODB_VERIFIED_BEHAVIOR.md`](docs/VIDEODB_VERIFIED_BEHAVIOR.md).

1. **An empty search is raised, not returned.** VideoDB signals "nothing
   matched" with `InvalidRequestError("No results found.")`. Mapping that to a
   provider failure would report a legitimate absence as an error, collapsing
   the distinction this product exists to preserve.
2. **Scene indexing is asynchronous despite returning an id.** `index_scenes()`
   returned in 3.1s for a 555s video; searches then failed for minutes before
   56 records appeared. AdProof now waits on `list_scene_index()` status **and**
   a non-zero record count.
3. **`time` in `extraction_config` must be a positive integer.** The default was
   `2.0`, a float, which would have failed every visual index.
4. **A fixed retrieval cap silently truncated a measurement.** `result_threshold=50`
   reported 100.1s of visible time when the true figure was 315.5s. The cap is
   now derived from media duration, and saturation is detected: a truncated
   shortfall becomes `uncertain`, never `fail`.
5. **`index_scenes()` creates duplicates.** Re-requesting an index with an
   identical name produced a second index rather than the documented error, so
   application-level deduplication is load-bearing.

Also worth knowing: keyword search returns a score of exactly `1.0` on every
hit while semantic search returns `0.47`-`0.69`. They are not the same scale,
which is why confidence bands are derived per index and never compared across
index types.

## Running it

Requires Python 3.12, PostgreSQL, and a VideoDB API key.

```bash
cd app
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
createdb adproof
cp .env.example .env          # add VIDEODB_API_KEY

# provision an account (no self-service signup by design)
./.venv/bin/python scripts/manage_users.py create-workspace "Your Agency"
./.venv/bin/python scripts/manage_users.py create-user you@example.com "You" <workspace-id> workspace_admin
```

Two processes. The worker is separate because provider calls block.

```bash
# terminal 1
./.venv/bin/uvicorn adproof.api.main:app --port 8000 --reload --reload-dir src
# terminal 2
./.venv/bin/python -m adproof.orchestrator.worker
```

Then <http://127.0.0.1:8000>. Landing page at `/`, application at `/app`.

### Verify the integration independently

```bash
./.venv/bin/python scripts/verify_videodb_spike.py "<video-url>" "<phrase>" "<visible thing>"
```

Prints the raw provider responses and resolves each open assumption. Writes
nothing to the database.

### Tests

```bash
createdb adproof_test && ./.venv/bin/python -m pytest
```

167 tests. Postgres-backed ones skip if no database is reachable.

| File | Covers |
| --- | --- |
| `test_intervals.py` | merge, dedupe, idempotency, no double counting |
| `test_evaluators.py` | every rule type, every absence class, every policy |
| `test_policy.py` | the adjudication gate |
| `test_integrity.py` | no fixture fallback, no invented SDK usage, measurement purity, mandatory provenance |
| `test_authorization.py` | cross-tenant isolation, SSRF rejection on the media proxy |
| `test_review.py` | override reasons, immutability, role separation |
| `test_insights.py` | revision drafting restraint, analytics reconciliation |

## Architecture

```
api/            HTTP, auth, workspace authorization, media proxy
orchestrator/   durable job queue; blocking provider calls run here
providers/      the ONLY module importing the VideoDB SDK
retrieval/      rule -> versioned searches + focused index prompts
evaluation/     PURE: intervals, absence, confidence, evaluators
policy.py       adjudication gate. Recommends; never decides.
revisions.py    creator-facing instructions, grounded in evidence
analytics.py    aggregation over recorded facts
```

Media playback is proxied. VideoDB stream URLs were verified to return `200`
with no credential, so AdProof issues a short-lived token bound to one user and
one asset, and the provider URL is never sent to a browser.

## Honest limitations

- **Only two rule types have run against real media.** Disclosure, competitor
  and prohibited-claim rules are implemented and unit-tested but have not been
  validated on footage that actually contains those things.
- **Confidence bands are uncalibrated** and must not be read as probabilities.
- **No resubmission.** Every submission has one version, so the
  request-changes loop stops at the message.
- **No schema migrations.** Development uses `create_all`.
- **Brief-to-rule extraction, evidence reels and exports are designed but not
  built.**

Nothing above is hidden in the product: `/api/integrity` reports the same list,
and the interface renders it.
