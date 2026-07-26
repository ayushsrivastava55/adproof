# API and Events Specification

This document defines product-level contracts and behavior, not programming-language signatures.

## 1. API design principles

- workspace-scoped authorization;
- idempotency for mutating operations;
- explicit asynchronous operation status;
- versioned resources;
- stable internal identifiers;
- provider details hidden behind integration resources;
- pagination for collections;
- actionable errors;
- auditability.

## 2. Resource groups

### Workspaces

- workspace details;
- policies;
- retention;
- usage;
- integrations.

### Campaigns

- create;
- read;
- update draft metadata;
- archive;
- list;
- analytics summary.

### Brief versions

- create;
- retrieve;
- compare;
- extract candidate rules.

### Rule sets

- create draft;
- edit;
- validate;
- confirm;
- supersede;
- compare versions.

### Submissions

- create;
- list;
- retrieve;
- add version;
- cancel processing where safe;
- archive.

### Processing

- status;
- job history;
- retry;
- diagnostics for authorized users.

### Reports

- retrieve latest;
- retrieve version;
- list rule results;
- retrieve evidence;
- request re-evaluation;
- request evidence reel.

### Reviews

- start;
- confirm rule;
- override rule;
- escalate;
- request changes;
- complete final decision.

### Exports

- request;
- status;
- retrieve;
- revoke.

## 3. Error categories

- validation error;
- unauthorized;
- forbidden;
- not found;
- conflict;
- unsupported rule;
- media unavailable;
- integration unavailable;
- indexing failed;
- processing incomplete;
- evidence expired;
- idempotency conflict;
- retention restriction;
- rate limited.

## 4. Asynchronous operation response

An asynchronous request should return:

- operation ID;
- operation type;
- current status;
- related resource;
- creation time;
- next recommended polling time or event expectation;
- human-readable status.

## 5. Event model

### Campaign events

- campaign.created;
- campaign.updated;
- campaign.archived;
- ruleset.draft_created;
- ruleset.confirmed;
- ruleset.superseded.

### Submission events

- submission.created;
- submission.version_added;
- submission.ingestion_started;
- submission.ingestion_completed;
- submission.indexing_started;
- submission.indexing_completed;
- submission.evaluation_started;
- submission.review_ready;
- submission.processing_failed;
- submission.changes_requested;
- submission.approved;
- submission.rejected;
- submission.escalated.

### Report events

- report.created;
- report.superseded;
- evidence_reel.requested;
- evidence_reel.completed;
- evidence_reel.failed.

## 6. Webhook behavior

External webhooks should support:

- signed payloads;
- timestamp validation;
- retry;
- idempotent consumption;
- event ID;
- event version;
- workspace reference;
- related resource;
- delivery attempt history.

## 7. Integration status

The API must expose whether a report is based on:

- live provider processing;
- incomplete processing;
- imported external evidence;
- explicitly labelled test fixture.

A production report must never silently depend on test fixtures.
