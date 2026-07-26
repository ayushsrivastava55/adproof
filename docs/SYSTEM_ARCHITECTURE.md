# System Architecture

## 1. Architectural goals

- real evidence, not generated placeholders;
- asynchronous media processing;
- durable audit history;
- clear separation of retrieval, evaluation, and adjudication;
- vendor integration behind an explicit adapter boundary;
- deterministic rule evaluation;
- versioned rules and reports;
- secure multi-tenant isolation;
- recoverable jobs;
- observable processing.

## 2. Logical components

### Web application

Responsibilities:

- campaign and rule management;
- submission workflow;
- processing status;
- evidence player;
- human review;
- analytics;
- administration.

### Application API

Responsibilities:

- authorization;
- domain validation;
- campaign and submission operations;
- orchestration;
- report queries;
- review actions;
- integration endpoints.

### Processing orchestrator

Responsibilities:

- schedule ingestion;
- create indexes;
- poll or receive asynchronous status;
- launch retrieval;
- launch deterministic evaluation;
- retry safe failures;
- stop duplicate work;
- record job history.

### VideoDB adapter

Responsibilities:

- connect to VideoDB;
- manage collection and video references;
- request spoken indexes;
- request scene indexes;
- search indexes;
- obtain playable streams;
- create evidence clips or compositions;
- normalize provider responses into internal representations.

The rest of the product should not depend directly on provider-specific response shapes.

### Rule extraction service

Responsibilities:

- parse plain-language briefs;
- propose structured rules;
- preserve source spans;
- identify ambiguity;
- identify contradictions;
- mark unsupported or subjective requirements.

It may propose. It may not activate rules.

### Evidence retrieval service

Responsibilities:

- translate rules into focused retrieval requests;
- search supporting evidence;
- search conflicting evidence;
- normalize timestamps;
- remove duplicates;
- attach provenance.

### Deterministic evaluation engine

Responsibilities:

- merge intervals;
- calculate visible duration;
- count occurrences;
- validate time windows;
- evaluate required and forbidden conditions;
- enforce configured confidence and absence policies;
- produce machine results.

### Adjudication and policy service

Responsibilities:

- determine whether human review is required;
- apply campaign approval policy;
- generate recommendations;
- validate reviewer permissions;
- record overrides;
- produce final status.

### Relational database

Stores:

- workspaces;
- users and roles;
- campaigns;
- brief versions;
- rule-set versions;
- submissions;
- media references;
- processing jobs;
- indexes;
- evidence;
- evaluations;
- reviews;
- audit events;
- integration references.

### Object storage

Stores non-VideoDB artifacts where required:

- original brief attachments;
- exported reports;
- customer-provided reference images;
- policy documents;
- temporary import files.

### Event and job system

Required for:

- ingestion requested;
- ingestion completed;
- index requested;
- index completed;
- evaluation requested;
- evaluation completed;
- review required;
- review completed;
- export requested.

## 3. Separation of concerns

### Retrieval is not evaluation

A search result means potentially relevant media was retrieved. It is not automatically proof that the condition is satisfied.

### Evaluation is not final approval

A machine result applies configured logic to retrieved evidence. Final approval depends on workspace policy.

### Explanation is not evidence

Generated text may explain a result. Only linked media and recorded metadata are evidence.

### Absence is not failure by default

No result may mean:

- requirement not present;
- poor extraction;
- unsupported language;
- low media quality;
- indexing incomplete;
- query mismatch;
- service failure.

Absence-based rules need explicit policy.

## 4. Multi-tenancy

Every domain record must belong to a workspace.

Workspace isolation applies to:

- campaigns;
- media references;
- rules;
- evidence;
- reports;
- users;
- exports;
- integrations.

Provider-side collections should follow a documented workspace or campaign isolation strategy.

## 5. Versioning

Version:

- campaign briefs;
- rule sets;
- individual rule definitions;
- retrieval prompts;
- visual index prompts;
- evaluation policies;
- reports;
- evidence reels.

A completed report must identify the exact versions used.

## 6. Idempotency

Idempotency is required for:

- submission creation;
- media ingestion requests;
- index creation requests;
- evaluation requests;
- webhook processing;
- external status updates;
- final decision updates.

## 7. Failure handling

Each external operation should have:

- queued state;
- running state;
- completed state;
- retryable failure;
- terminal failure;
- cancellation where supported;
- timestamps;
- attempt count;
- provider reference;
- visible error summary;
- internal diagnostic detail.

## 8. Observability

Track:

- processing duration;
- queue delay;
- provider latency;
- index failures;
- empty-result rates;
- evidence playback failures;
- rule-evaluation errors;
- override rates;
- retries;
- cost by workspace and campaign.

## 9. Deployment posture

Begin with a modular monolith and a durable job mechanism. Preserve boundaries so media processing can later scale independently.

Do not introduce distributed complexity before processing volume or reliability requires it.
