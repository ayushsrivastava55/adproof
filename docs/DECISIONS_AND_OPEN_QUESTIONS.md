# Decisions and Open Questions

## Confirmed product decisions

### The product is evidence-first

Every meaningful automated conclusion links to exact media evidence.

### Human confirmation activates rules

Rule extraction produces proposals, not obligations.

### Evaluation is deterministic where measurable

Duration, counts, thresholds, order, and state transitions are calculated by application logic.

### VideoDB is the primary media layer

VideoDB is used for media ingestion, indexing, retrieval, playback, and supported composition workflows.

### Missing evidence is not proof of absence

Absence policy is configured by rule type.

### Original artifacts are preserved

Original briefs, rule versions, submission versions, and automated results remain auditable.

### Mock behavior must be explicit

No invisible fallback from real integration to fixture data.

## Open product questions

### Target first customer

Which segment has the strongest urgency and shortest sales cycle?

- influencer agency;
- creator marketplace;
- consumer brand;
- affiliate network.

### Review policy defaults

Should the initial default require a human final decision for every submission?

### Disclosure scope

Which regions and disclosure standards should templates support first?

### Creator-facing feedback

Should creators see raw machine findings, reviewer-confirmed findings only, or a simplified revision list?

### Reference assets

How should customers provide product packaging references and competitor references?

### Multi-language scope

Which languages and code-switching patterns are required initially?

### Evidence retention

What default retention balances customer value, privacy, and cost?

### Workspace-to-collection mapping

Should VideoDB collections map to workspace, campaign, or another isolation boundary?

### Report scoring

Does an overall percentage improve decisions, or does it create false precision?

### Auto-rejection

Should any category ever support automatic rejection without human review?

## Decision log

### 2026-07-26 — Pin `videodb==0.5.1` and resolve the SDK surface conflict

**Context.** Three current VideoDB documentation pages describe three different
SDK surfaces for indexing and search.

**Decision.** Pin the SDK exactly and treat **source introspection of the pinned
version** as authoritative over the prose documentation. Use
`index_spoken_words` / `index_scenes` / `legacy_search`, because these let each
rule name its exact index and search mode, which is what makes retrieval
reproducible and versioned.

**Consequences.** A version bump requires re-running the verification spike.
`app/tests/test_integrity.py` asserts every SDK method and constant used, so an
incompatible bump fails the build. Full evidence in `VIDEODB_VERIFIED_BEHAVIOR.md`.

**Documents affected.** `VIDEODB_INTEGRATION.md`, `VIDEODB_VERIFIED_BEHAVIOR.md`.

### 2026-07-26 — Asynchronous state is derived from AdProof's own job records

**Context.** `videodb 0.5.1` polls internally and blocks (default 5s interval,
500s budget). It exposes no method to poll an index job by id, and no progress
signal.

**Decision.** Run every blocking provider call inside a worker job. The
`processing_job` row is the authority on stage state. Do not claim to poll
VideoDB, and display no progress percentage.

**Consequences.** Per-stage status is honest but coarse. A polling timeout is
reported as "may still be building on the provider", never as a failure. Stated
in `/api/integrity` and in the UI.

### 2026-07-26 — Absence may cause failure only for exact spoken matches

**Context.** `VERIFICATION_ENGINE.md` s8 permits absence-based failure for exact
keyword checks but requires human review by default for blocking decisions.

**Decision.** Three policies: `uncertain` (default), `require_human_review`, and
`fail_when_coverage_complete`. The last may produce `fail` **only** when the
absence classifies as `likely_absent`, which only an exact keyword search over a
completed index can produce. A semantic visual miss can never fail, whatever the
configured policy. A provider failure never produces a content verdict at all.

**Consequences.** Visual rules cannot auto-fail in this build. Deliberate.

### 2026-07-26 — Measurement resolution is reported, and gates near-threshold verdicts

**Context.** Time-based scene extraction samples every N seconds, which bounds
achievable duration accuracy.

**Decision.** Use time-based extraction (fixed, known granularity) rather than
shot-based (content-dependent, unknown). Persist the granularity, display it,
and return `uncertain` when the measured duration is within one sampling
interval of the threshold.

**Consequences.** Borderline visual-duration rules route to humans rather than
producing a verdict the measurement cannot support.

### 2026-07-26 — Semantic hits are retrieved but excluded from measurement

**Context.** Semantic search finds paraphrases. An exact-phrase requirement is
not satisfied by a paraphrase.

**Decision.** Every retrieval run carries `counts_toward_measurement`. For a
required spoken phrase, only the exact keyword run counts; the semantic run is
retrieved as reviewer context and labelled as such in the UI.

**Consequences.** Semantic drift cannot manufacture a pass. Reviewers still see
near misses.

### 2026-07-26 — Review workspace: the adjudication gate

**Context.** VERIFICATION_ENGINE.md s9 requires a policy-driven recommendation;
PRD s12 requires that policy, not the machine, decides whether human approval is
mandatory.

**Decision.** Three separations are enforced:

1. **Recommendation is not decision.** `policy.adjudicate()` returns a
   suggestion plus the set of decisions currently permitted. A person takes the
   action; nothing is ever auto-approved or auto-rejected.
2. **Approval requires resolution.** A submission cannot be approved or
   rejected while any rule is failing, uncertain, errored, or awaiting human
   review without a reviewer having taken a position. Reviewers can still
   request changes or escalate, so nobody is stuck.
3. **Absence cannot drive rejection.** A blocking failure whose result rests on
   an absence class produces `request_changes`, not `reject`, until a human has
   reviewed it. This carries the product's central promise into the decision
   layer.

**Consequences.** Overriding a rule changes the *effective* state at read time;
the machine result is never mutated and stays visible beside it. Reviews and
decisions are append-only at the database level, so a correction is a new row.
Agreement with the machine recommendation is stored per decision, making
override rate measurable for QUALITY_AND_EVALUATION.md s10.

**Role split.** Final approve/reject is restricted to workspace admins and
campaign managers, per the DATA_MODEL.md role model; reviewers may confirm,
override, request changes, and escalate.

## Technical questions to verify against current docs

Status as of 2026-07-26. Full detail in `VIDEODB_VERIFIED_BEHAVIOR.md`.

- ~~exact VideoDB asynchronous index status model~~ — **resolved**: no pollable
  status exists in `videodb 0.5.1`; the SDK blocks and polls internally;
- exact collection metadata filtering behavior — **still open**; not needed by
  the Phase 1 slice, which searches per video;
- ~~current search result schema~~ — **resolved**: `SearchResult.shots[]` with
  `start`, `end`, `text`, `search_score`, `scene_index_id`, `stream_url`;
- stream URL lifetime and authorization behavior — **partially open**: docs say
  URLs live as long as the video is stored; whether they are access controlled
  is **unverified and assumed unsafe** (assumption A-10);
- ~~current scene-index prompt and extraction options~~ — **resolved**:
  `extraction_type`, `extraction_config{time, frame_count, select_frames}`,
  `prompt`, `name`, `model_name`;
- current media composition workflow — **still open**; needed for Phase 7
  evidence reels, not for this slice;
- provider retry and rate-limit behavior — **still open**; AdProof applies its
  own bounded retries (default 3 attempts);
- provider deletion semantics — **still open**; needed for retention work;
- provider region and retention options — **still open**.

## Decision log template

For each new material decision record:

- date;
- decision;
- context;
- considered options;
- rationale;
- consequences;
- owner;
- documents affected;
- review date.
