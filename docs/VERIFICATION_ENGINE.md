# Verification Engine Specification

## 1. Purpose

The verification engine transforms confirmed campaign rules and retrieved video evidence into auditable machine results.

It must avoid turning probabilistic retrieval into unsupported certainty.

## 2. Processing stages

1. Load confirmed rule-set version.
2. Confirm required indexes are complete.
3. Generate versioned retrieval plan.
4. Retrieve supporting evidence.
5. Retrieve conflicting evidence.
6. Normalize and deduplicate evidence.
7. Apply deterministic evaluator.
8. Apply confidence and absence policy.
9. Produce machine result.
10. Route result through adjudication policy.
11. Persist report and provenance.

## 3. Rule structure

Each rule should include:

- stable rule ID;
- rule-set version;
- source brief reference;
- normalized requirement;
- type;
- modality;
- target entity;
- required phrases or concepts;
- forbidden phrases or concepts;
- threshold;
- time window;
- occurrence requirement;
- severity;
- automatable status;
- human-review requirement;
- absence policy;
- confidence policy;
- retrieval plan version;
- evaluator version;
- reviewer guidance.

## 4. Evidence classes

### Supporting evidence

Media moments indicating that a requirement may be satisfied.

### Conflicting evidence

Media moments indicating violation, contradiction, or a competing interpretation.

### Context evidence

Nearby moments necessary to interpret a retrieved segment.

### Negative evidence

Evidence produced by a dedicated method designed to establish absence. This is stronger than an empty search response and should be used sparingly.

## 5. Deterministic evaluator families

### Minimum duration

Used for product visibility, logo visibility, disclosure duration, and similar conditions.

Process:

- normalize timestamp ranges;
- discard invalid ranges;
- merge overlapping and adjacent ranges according to configured tolerance;
- optionally intersect with a required time window;
- sum duration;
- compare against threshold.

### Maximum duration

Used where an element may appear only briefly or not exceed a limit.

### Required occurrence

Count valid distinct occurrences after deduplication.

### Forbidden occurrence

Fail when a qualifying occurrence is found above configured confidence and severity policy.

### Required-in-window

Check whether evidence appears within a specific interval, such as the first ten seconds.

### Sequence

Check ordered events using timestamps and allowed gap.

### Either-modality requirement

Pass when the requirement is validly satisfied by spoken or visual evidence.

### Both-modalities requirement

Pass only when both configured modalities are satisfied.

## 6. Interval policy

Define:

- minimum segment length;
- merge tolerance;
- overlap handling;
- adjacent interval handling;
- whether context padding counts;
- whether duplicate indexes can contribute duplicate duration;
- how video boundaries are handled.

Context padding must never count toward measured visibility.

## 7. Confidence policy

Confidence may come from:

- provider retrieval score;
- extraction confidence;
- agreement across queries;
- agreement across indexes;
- exact keyword match;
- human confirmation;
- media quality.

Do not collapse unrelated confidence signals into a meaningless single number without calibration.

Recommended initial display:

- high confidence;
- medium confidence;
- low confidence;
- unavailable.

Internal numeric values may be stored for evaluation and calibration.

## 8. Absence policy

Rule-specific options:

- absence never causes automatic failure;
- absence causes uncertain;
- absence causes fail only when processing coverage is complete and confidence is high;
- absence causes fail for exact spoken keyword checks;
- absence requires a second retrieval strategy;
- absence always requires human review.

Blocking decisions based on absence should default to human review.

## 9. Overall submission recommendation

The overall recommendation should be policy-driven.

Example policy concepts:

- any confirmed blocking failure recommends rejection;
- any unresolved blocking rule prevents approval;
- required-rule failures recommend changes requested;
- subjective criteria require human review;
- processing errors prevent completion;
- optional-rule failures do not block approval;
- a configured minimum score may supplement but not replace rule logic.

## 10. Explanation generation

Explanations must be grounded in structured results.

An explanation may state:

- what was required;
- what evidence was found;
- measured duration or count;
- what threshold applied;
- why the state is uncertain;
- what the reviewer should inspect.

It must not add facts not present in the evidence or rule.

## 11. Overrides

A reviewer override records:

- previous machine result;
- new human result;
- reason category;
- free-text explanation;
- reviewer;
- time;
- evidence viewed;
- policy exception where applicable.

The original machine result remains immutable.

## 12. Re-evaluation

A report may be re-evaluated when:

- rule set changes;
- retrieval plan changes;
- index improves;
- provider processing completes;
- evaluator version changes;
- media is resubmitted.

Each re-evaluation creates a new report version.

## 13. Unsupported rules

Rules that cannot be reliably automated must remain visible and be routed to humans.

Examples:

- “feel premium”;
- “look authentic”;
- “match the campaign vibe”;
- “be sufficiently enthusiastic.”

A future rubric system may partially structure these, but the product must not pretend subjectivity has become objective.
