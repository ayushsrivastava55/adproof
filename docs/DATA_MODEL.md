# Data Model

## Core entities

### Workspace

Represents a customer or organizational boundary.

Key concepts:

- name;
- plan;
- region;
- retention policy;
- default review policy;
- created time.

### User

Represents an authenticated person.

### Membership

Connects users to workspaces with roles and permissions.

### Brand

Reusable brand identity, products, reference assets, and terminology.

### Product

Target product with names, variants, packaging references, approved claims, prohibited claims, and competitor category.

### Campaign

Commercial campaign containing channels, dates, brand, product, and operational settings.

### Campaign brief version

Immutable snapshot of original and normalized brief content.

### Rule-set version

Immutable confirmed group of rules used to evaluate submissions.

### Rule

Structured requirement within a rule set.

### Creator

Workspace-local creator record or external reference.

### Submission

A creator’s submitted deliverable associated with a campaign.

### Submission version

Immutable media version and metadata.

### Media asset

Internal record mapping to VideoDB provider references.

### Processing job

Tracks ingestion, indexing, retrieval, evaluation, composition, or export work.

### Media index

Tracks spoken or visual index configuration, status, provider reference, and version.

### Retrieval run

Records the exact search plan and provider interaction used to retrieve evidence.

### Evidence item

Timestamped media evidence with provenance.

### Evaluation result

Machine result for one rule.

### Submission report

Versioned collection of evaluation results and overall recommendation.

### Review

Human review action.

### Decision

Final or intermediate operational decision.

### Evidence reel

Derived video artifact linked to report and evidence versions.

### Audit event

Append-only record of material actions.

### Integration

Workspace configuration for external systems.

## Important relationships

- Workspace has many campaigns.
- Campaign has many brief versions.
- Campaign has many rule-set versions.
- Rule-set version has many rules.
- Campaign has many submissions.
- Submission has many submission versions.
- Submission version has one or more media assets.
- Media asset has many indexes.
- Rule and submission version produce retrieval runs.
- Retrieval runs produce evidence.
- Evidence and rule produce evaluation result.
- Evaluation results form a submission report.
- Submission report has reviews and decisions.
- Material changes produce audit events.

## Versioning rules

Immutable after use:

- brief version;
- confirmed rule-set version;
- submission version;
- completed retrieval run;
- completed evaluation result;
- completed report;
- review action;
- audit event.

Changes create new versions rather than overwriting history.

## Provider references

Provider IDs must never be used as user-facing authorization boundaries.

Store:

- provider;
- provider collection ID;
- provider video ID;
- provider index ID;
- provider job ID;
- provider stream reference;
- raw status;
- normalized status.

## Data retention

Retention policy must define:

- original media retention;
- derived evidence retention;
- provider asset deletion;
- report retention;
- audit retention;
- export retention;
- deletion verification.

## Suggested role model

### Workspace Administrator

Full workspace settings and integrations.

### Campaign Manager

Campaign and rule management, final decisions.

### Reviewer

Evidence review, confirm, override, request changes.

### Compliance Reviewer

Review restricted rule categories.

### Analyst

Read reports and analytics.

### Creator Submitter

Create and view permitted submissions only.

## Audit event categories

- campaign created;
- brief updated;
- rules proposed;
- rules confirmed;
- submission created;
- media ingested;
- index completed;
- evaluation completed;
- review started;
- rule overridden;
- changes requested;
- approved;
- rejected;
- escalated;
- export created;
- media deleted;
- retention action completed.
