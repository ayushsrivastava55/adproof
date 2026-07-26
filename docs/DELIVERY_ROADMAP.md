# Delivery Roadmap

The roadmap is organized around verifiable vertical slices. Each phase must preserve product integrity and must not present unfinished integrations as complete.

## Phase 1: Domain and workflow foundation

Outcomes:

- workspace, campaign, brief, rule-set, submission, and review concepts;
- explicit states;
- versioning strategy;
- audit event design;
- product navigation;
- error language.

Exit criteria:

- campaign and submission lifecycle is documented and internally consistent;
- no unresolved contradiction in core domain states.

## Phase 2: Real VideoDB integration proof

Outcomes:

- real video ingestion;
- real playable stream;
- spoken index;
- focused visual index;
- timestamped spoken search;
- timestamped visual search;
- normalized provider responses;
- visible integration failures.

Exit criteria:

- one real media asset completes the end-to-end provider flow;
- no mock output appears in a live execution path;
- exact provider assumptions are documented.

## Phase 3: Confirmed structured rules

Outcomes:

- manual rule creation;
- rule templates;
- rule-set versions;
- validation;
- source brief linkage;
- subjective rule routing.

Exit criteria:

- a campaign manager can create and confirm a measurable rule set without automatic extraction.

## Phase 4: Evidence retrieval and deterministic evaluation

Outcomes:

- rule retrieval plans;
- supporting and conflicting evidence;
- interval normalization;
- duration and occurrence evaluation;
- uncertainty and absence policy;
- rule-level result states.

Exit criteria:

- representative rules produce reproducible results with timestamped evidence.

## Phase 5: Review workspace

Outcomes:

- evidence player;
- rule list;
- provenance;
- confirm;
- override;
- escalate;
- request changes;
- final decision.

Exit criteria:

- reviewer can complete a defensible decision without manually navigating raw provider tools.

## Phase 6: Brief-to-rule assistance

Outcomes:

- candidate rule extraction;
- source-span preservation;
- ambiguity detection;
- contradiction detection;
- human confirmation.

Exit criteria:

- generated rules never become active automatically;
- unsupported and subjective requirements are routed correctly.

## Phase 7: Evidence reels and exports

Outcomes:

- selected evidence composition;
- evidence labels;
- report export;
- share and expiry controls.

Exit criteria:

- reel content links to report evidence and versions;
- export does not overstate machine conclusions.

## Phase 8: Campaign analytics

Outcomes:

- failure patterns;
- review time;
- resubmissions;
- override analysis;
- processing health.

Exit criteria:

- aggregates reconcile with underlying reports;
- incomplete jobs are not silently included.

## Phase 9: Integrations and scale

Outcomes:

- external submission API;
- signed webhooks;
- batch imports;
- role administration;
- retention automation;
- cost and usage controls.

## Delivery discipline

At the end of every phase:

- update decisions;
- update open questions;
- record real service limitations;
- record evaluation results;
- run integrity review;
- remove or label temporary fixtures;
- confirm error states;
- confirm no user-facing fabricated data.
