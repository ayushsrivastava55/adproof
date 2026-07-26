# Security and Privacy

## 1. Security objectives

- prevent cross-workspace media access;
- protect unpublished creator content;
- minimize unnecessary data retention;
- preserve audit integrity;
- secure external integrations;
- prevent unauthorized decisions;
- ensure evidence links respect access policy.

## 2. Authorization

Authorization must be checked at the application layer for every resource.

Provider asset IDs or stream URLs do not grant application permission.

## 3. Media access

- use authorized uploads or source URLs;
- avoid public links where private delivery is possible;
- expire or revoke temporary access;
- validate workspace ownership before issuing playback references;
- log sensitive exports;
- restrict download according to workspace policy.

## 4. Secrets

Store credentials in managed secrets systems.

Never:

- commit API keys;
- place keys in documentation;
- expose provider credentials to the browser;
- log complete secrets;
- place secrets in generated reports.

## 5. Data minimization

Collect only data needed for:

- campaign operations;
- review;
- audit;
- integration;
- customer-configured analytics.

Avoid collecting sensitive creator information unrelated to verification.

## 6. Retention and deletion

Workspace policy should define:

- original video retention;
- provider-side deletion;
- derived clip retention;
- report retention;
- audit retention;
- deletion grace periods;
- legal hold behavior.

Deletion must propagate to external providers where required and produce a verifiable completion record.

## 7. Audit integrity

Material audit events should be append-only.

Corrections should create new events rather than modifying prior history.

## 8. External source rights

The customer must have authority to submit, process, and review the media.

The product should record:

- source type;
- uploader;
- authorization attestation where appropriate;
- source URL;
- submission time.

## 9. Sensitive rule categories

Health, financial, political, child-directed, and regulated-product claims may require:

- specialist review;
- stricter retention;
- restricted reviewer roles;
- region-specific policy;
- disabled auto-approval.

## 10. Model and provider data handling

Before production rollout, document:

- what content is sent to each provider;
- provider retention;
- provider training policy;
- region availability;
- deletion behavior;
- subprocessors;
- incident handling.

## 11. Threats to address

- cross-tenant object access;
- insecure direct object references;
- leaked stream links;
- forged webhooks;
- replayed events;
- malicious files;
- prompt injection inside transcripts or on-screen text;
- reviewer privilege escalation;
- audit log tampering;
- denial of service through oversized media;
- unexpected cost amplification.

## 12. Prompt injection posture

Video transcripts and on-screen text are untrusted content.

Instructions contained inside media must never modify system behavior, permissions, tools, policies, or evaluation criteria.
