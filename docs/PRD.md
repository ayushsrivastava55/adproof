# Product Requirements Document

## 1. Product overview

AdProof is a video-native campaign verification platform that helps brands, agencies, creator marketplaces, and affiliate networks review creator-submitted media against agreed campaign requirements.

The system ingests campaign briefs and creator videos, converts the brief into reviewable structured rules, analyzes spoken and visual content, and generates an evidence-backed compliance report.

The primary product object is not the video itself. It is the relationship between:

- a commercial requirement;
- the media submitted to satisfy it;
- machine-retrieved evidence;
- deterministic evaluation;
- human adjudication;
- an auditable final decision.

## 2. Problem statement

Campaign operations teams frequently review creator content manually. Requirements are scattered across briefs, email threads, spreadsheets, messaging apps, contracts, and platform notes.

A reviewer must determine whether a submission:

- displays the correct product;
- displays it for long enough;
- communicates required talking points;
- includes a disclosure;
- uses the correct discount code;
- avoids prohibited claims;
- avoids competitor appearances;
- follows required product-use instructions;
- uses approved branding;
- satisfies channel-specific requirements.

Manual review creates several business problems:

- high operating cost;
- slow campaign turnaround;
- inconsistent reviewer decisions;
- weak evidence for disputes;
- difficulty managing large creator volumes;
- poor historical visibility;
- avoidable reshoots and delayed payouts;
- limited ability to learn which requirements repeatedly fail.

## 3. Product goals

### 3.1 Primary goals

- Reduce time spent manually reviewing creator submissions.
- Make every automated finding inspectable through exact video evidence.
- Standardize repeatable and measurable campaign checks.
- Route ambiguous findings to humans instead of forcing false certainty.
- Create an audit trail for review, overrides, and approval decisions.
- Support campaign-level and creator-level operational intelligence.
- Provide an integration layer for external campaign management systems.

### 3.2 Secondary goals

- Help campaign managers write more measurable briefs.
- Reduce unnecessary reshoot cycles.
- Identify recurring failure patterns across creators and campaigns.
- Generate concise evidence reels for stakeholders.
- Support quality assurance before a video is published.
- Support post-publication verification where permitted.

## 4. Non-goals

The initial product will not:

- autonomously interpret legal obligations without human confirmation;
- guarantee legal or regulatory compliance;
- autonomously issue payments;
- score creative quality as an objective fact;
- judge subjective requirements without a configured review rubric;
- infer creator intent;
- perform audience-performance attribution;
- replace rights management or content licensing systems;
- identify people using biometric recognition;
- scrape private social accounts without authorization.

## 5. Target customers

### 5.1 Influencer marketing agencies

Need to process many submissions across brands, creators, reviewers, and campaign timelines.

### 5.2 Consumer brands

Need a consistent verification layer for internal and agency-managed creator campaigns.

### 5.3 Creator marketplaces

Need submission review, proof of completion, dispute handling, and scalable approval operations.

### 5.4 Affiliate and ambassador programs

Need to verify codes, product demonstrations, required disclosures, and prohibited statements.

### 5.5 Regulated campaign teams

Need stronger evidence, review controls, documented overrides, and mandatory human adjudication.

## 6. Primary personas

### Campaign Manager

Creates campaigns, defines requirements, assigns reviewers, tracks progress, and makes final operational decisions.

### Reviewer

Inspects evidence, confirms or overrides findings, requests changes, and records reasoning.

### Agency Administrator

Manages workspaces, templates, permissions, integrations, retention, and reporting.

### Creator or Submission Partner

Uploads media, sees validation feedback when permitted, and resubmits corrected content.

### Compliance or Legal Reviewer

Reviews sensitive rules, prohibited claims, disclosures, and exceptions.

## 7. Core user stories

- As a campaign manager, I can convert a campaign brief into structured rules.
- As a campaign manager, I can edit every generated rule before activation.
- As a reviewer, I can see each rule’s result and jump to exact evidence.
- As a reviewer, I can distinguish supporting evidence from conflicting evidence.
- As a reviewer, I can mark uncertain findings for a specialist.
- As a reviewer, I can override a machine result and record why.
- As an administrator, I can create reusable rule templates.
- As an agency, I can compare failure patterns across campaigns.
- As a creator, I can receive precise revision instructions.
- As an integration partner, I can submit videos and receive status events.

## 8. Core workflow

### Campaign creation

The user enters:

- campaign name;
- brand and product;
- campaign objective;
- distribution channels;
- target regions;
- start and end dates;
- plain-language campaign brief;
- reference assets;
- required disclosures;
- prohibited statements;
- competitor restrictions;
- approval policy;
- payout or completion policy.

### Rule generation and confirmation

The system proposes structured rules. Every proposal must show:

- source text from the brief;
- normalized requirement;
- rule type;
- modality;
- threshold;
- severity;
- automatable status;
- uncertainty;
- reviewer guidance.

A human confirms the rule set before it becomes active.

### Submission ingestion

The user or external system provides:

- creator identity or external creator reference;
- video file or authorized source URL;
- channel;
- version;
- submission notes;
- expected language;
- associated campaign;
- optional transcript or caption file.

### Indexing and analysis

The system:

- stores the VideoDB asset reference;
- indexes spoken content;
- creates one or more focused visual indexes;
- tracks asynchronous job status;
- retries according to policy;
- records failures visibly;
- searches for evidence;
- normalizes timestamps;
- evaluates deterministic conditions;
- creates a draft report.

### Human review

The reviewer:

- opens each requirement;
- plays exact evidence;
- sees confidence and limitations;
- confirms, overrides, or escalates;
- optionally requests a revision;
- records the final decision.

## 9. Rule categories

### Required spoken phrase

Examples:

- required brand phrase;
- required discount code;
- required product attribute;
- required call to action.

### Forbidden spoken phrase or claim

Examples:

- guaranteed outcome;
- unapproved medical claim;
- incorrect price;
- banned comparison.

### Minimum visual duration

Examples:

- product visible for at least eight seconds;
- logo visible for at least three seconds.

### Required visual event

Examples:

- creator opens the package;
- creator demonstrates use;
- product appears in the first ten seconds.

### Forbidden visual event

Examples:

- competitor packaging visible;
- unsafe use;
- restricted object present.

### Disclosure requirement

Examples:

- spoken disclosure;
- visible disclosure;
- either spoken or visible disclosure;
- disclosure present within a configured time window.

### Sequence requirement

Examples:

- hook before product reveal;
- product demonstration before call to action.

### Placement requirement

Examples:

- logo readable;
- product not obscured;
- packaging front visible.

### Subjective human-review criterion

Examples:

- energetic tone;
- premium feel;
- authentic delivery.

These must never be forced into objective pass or fail without a defined rubric.

## 10. Result states

Each rule may be:

- `pass`;
- `fail`;
- `uncertain`;
- `not_evaluated`;
- `human_review_required`;
- `processing`;
- `error`.

Each submission may be:

- `draft`;
- `ingesting`;
- `indexing`;
- `evaluating`;
- `ready_for_review`;
- `changes_requested`;
- `approved`;
- `rejected`;
- `escalated`;
- `error`;
- `archived`.

## 11. Evidence requirements

Every evidence item must include:

- source video reference;
- start timestamp;
- end timestamp;
- modality;
- index or retrieval source;
- retrieval query or rule linkage;
- extraction or search version;
- confidence where available;
- generated explanation;
- playable media reference;
- creation time.

A lack of evidence must be represented separately from evidence of absence.

## 12. Decision policy

The system can generate a recommendation, but the workspace policy determines whether human approval is mandatory.

Policy examples:

- auto-approve only when every required rule passes above configured confidence;
- always require review for disclosure and prohibited-claim rules;
- reject automatically only for configured unambiguous blocking violations;
- never auto-reject when the finding depends on absence;
- require two reviewers for regulated campaigns.

## 13. Reporting

### Submission report

- overall status;
- rule summary;
- supporting evidence;
- conflicting evidence;
- uncertainties;
- errors;
- reviewer decisions;
- overrides;
- final notes;
- evidence reel.

### Campaign report

- submission volume;
- pass rate;
- average review time;
- most frequently failed rules;
- resubmission rate;
- reviewer disagreement;
- creator-level trends;
- unresolved items.

### Workspace report

- media volume;
- processing reliability;
- review throughput;
- rule-template performance;
- false-positive and false-negative feedback;
- integration health.

## 14. Business model

Potential commercial models:

- usage-based pricing by analyzed media minute;
- workspace subscription plus included processing;
- creator-submission bundles;
- enterprise contracts;
- API plans;
- premium retention and audit controls;
- premium policy templates;
- paid implementation and integration services.

Avoid pricing solely per user because the principal cost and value scale with media volume and review operations.

## 15. Success metrics

### Product metrics

- median time from submission to review-ready report;
- median human review time;
- percentage of findings opened by reviewers;
- percentage of automated results overridden;
- resubmission reduction;
- campaign approval turnaround;
- evidence playback success rate.

### Quality metrics

- precision and recall by rule type;
- false-negative rate for blocking violations;
- agreement with expert reviewers;
- uncertain-routing accuracy;
- duration-calculation accuracy;
- disclosure detection accuracy;
- evidence timestamp usefulness.

### Business metrics

- processed media hours;
- active campaigns;
- repeat workspace usage;
- expansion from single campaign to workspace rollout;
- review-hours saved;
- retained monthly processing volume.

## 16. Initial release scope

The first release should support:

- workspace and campaign concepts;
- structured campaign rules;
- manual rule editing and confirmation;
- video submission;
- spoken-word indexing;
- focused visual indexing;
- rule-level evidence retrieval;
- deterministic duration and occurrence evaluation;
- timestamped playback;
- reviewer confirmation and override;
- submission-level report;
- audit history;
- clear processing and failure states.

## 17. Later expansion

- cross-video creator history;
- duplicate or reused footage detection;
- post-publication monitoring;
- channel-specific templates;
- multilingual rule packs;
- external campaign-management integrations;
- batch submissions;
- reviewer calibration;
- configurable policy engine;
- evidence export;
- creator revision portal;
- performance analytics integration.
