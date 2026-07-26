# AdProof Project Instructions

## Mission

Build AdProof as a production-minded video verification product for brands, agencies, creator marketplaces, and affiliate networks.

AdProof evaluates creator-submitted videos against campaign requirements and produces:

- structured rule-level results;
- timestamped supporting and conflicting evidence;
- confidence and uncertainty indicators;
- human-review workflows;
- approval and payout recommendations;
- short evidence reels for rapid review;
- historical campaign and creator intelligence.

## Read before making decisions

Always read these files before planning significant work:

- `docs/PRD.md`
- `docs/PRODUCT_PRINCIPLES.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/VIDEODB_INTEGRATION.md`
- `docs/VERIFICATION_ENGINE.md`
- `docs/QUALITY_AND_EVALUATION.md`
- `docs/DELIVERY_ROADMAP.md`
- `docs/DECISIONS_AND_OPEN_QUESTIONS.md`

## Product boundaries

AdProof is not:

- a generic chat-with-video product;
- a generic video summarizer;
- an autonomous legal decision-maker;
- a fully automated payment processor;
- a substitute for human review in ambiguous, subjective, or regulated cases;
- a product that should infer contractual obligations not present in the campaign brief.

## Core execution loop

1. A user creates a campaign.
2. The user defines or imports campaign requirements.
3. Requirements are converted into structured verification rules.
4. A human reviews and confirms those rules.
5. Creator videos are submitted and ingested.
6. Spoken and visual indexes are created.
7. The verification engine searches for supporting and conflicting evidence.
8. Deterministic evaluators calculate measurable conditions.
9. The system labels each rule as pass, fail, uncertain, not evaluated, or human review required.
10. The reviewer opens exact video moments and makes a final decision.
11. The system records the decision, rationale, overrides, and audit history.

## Critical integrity requirements

- Never fabricate evidence or service responses.
- Never invent VideoDB method names, fields, status values, or guarantees.
- Check the latest official VideoDB documentation before implementing integration behavior.
- Persist provenance for every piece of evidence.
- Distinguish machine findings from human decisions.
- Distinguish “not found” from “proven absent.”
- Do not convert low-confidence absence into a definitive failure without the configured policy.
- Do not let an LLM perform arithmetic for durations, occurrence counts, or thresholds.
- Do not silently ignore failed indexing jobs.
- Do not silently fall back to mocked data.
- Any simulation, fixture, or stub must be visibly labelled in both code and interface.
- Preserve the original media and original campaign brief as audit artifacts.

## Planning expectations

For each major work item:

1. Restate the user-visible outcome.
2. Identify assumptions and unresolved product decisions.
3. Verify relevant external API behavior.
4. Define acceptance criteria.
5. Identify failure states and recovery behavior.
6. Plan the smallest end-to-end vertical slice.
7. Implement and validate.
8. Review against the source documents.
9. Update project state and decisions.

## Decision hierarchy

When documents conflict, use this priority:

1. Product integrity and user safety
2. `CLAUDE.md`
3. `docs/PRODUCT_PRINCIPLES.md`
4. `docs/PRD.md`
5. Domain-specific specification
6. Delivery roadmap
7. Existing implementation

Do not preserve an existing implementation merely because it exists. Do not rewrite it merely because a different pattern is preferred. Make changes only when they materially improve correctness, integrity, maintainability, or product outcomes.

## Model usage

Claude Opus 5 should be used for:

- complex planning;
- cross-document reasoning;
- product-rule extraction design;
- architecture reviews;
- adversarial quality reviews;
- ambiguous workflow analysis;
- evidence-grounded explanations.

Deterministic application logic should handle:

- timestamps;
- interval merging;
- duration totals;
- occurrence counts;
- threshold comparison;
- state transitions;
- authorization;
- audit events;
- idempotency;
- retention policies.

## Completion standard

A capability is complete only when:

- its user journey works end to end;
- service failures are visible and recoverable;
- outputs include evidence provenance;
- measurable requirements are evaluated deterministically;
- uncertainty is represented honestly;
- the audit trail records automated and human actions;
- acceptance criteria are satisfied;
- documentation is updated;
- no placeholder behavior is presented as production behavior.
