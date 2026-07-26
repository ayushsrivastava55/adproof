# Quality and Evaluation Plan

## 1. Quality objective

Measure whether AdProof retrieves useful evidence and produces appropriately calibrated, auditable rule outcomes.

A polished interface cannot compensate for unreliable or unverifiable findings.

## 2. Evaluation layers

### Provider integration quality

- upload success;
- index completion;
- search availability;
- playback validity;
- asynchronous job reliability.

### Retrieval quality

- relevant evidence precision;
- relevant evidence recall;
- timestamp quality;
- duplicate rate;
- query consistency.

### Rule-evaluation quality

- threshold accuracy;
- occurrence accuracy;
- sequence accuracy;
- absence handling;
- correct uncertainty routing.

### Human workflow quality

- review time;
- evidence usefulness;
- override rate;
- reviewer agreement;
- resolution time.

## 3. Gold dataset

Create a curated dataset with:

- controlled positive examples;
- controlled negative examples;
- ambiguous examples;
- low-quality audio;
- small product appearance;
- background competitor appearance;
- multiple languages;
- code-switching;
- visible but unreadable disclosures;
- repeated clips;
- partial submissions;
- contradictory speech and visuals.

Every sample should have human-labelled:

- rule outcome;
- evidence ranges;
- ambiguity notes;
- reviewer rationale.

## 4. Metrics by rule type

### Required spoken phrase

- exact phrase precision and recall;
- semantic equivalent precision and recall;
- timestamp error;
- pronunciation variant handling.

### Visual product presence

- segment precision;
- segment recall;
- duration error;
- identity confusion;
- background versus active-use distinction.

### Disclosure

- visible-text detection;
- spoken detection;
- readability classification;
- duration calculation;
- placement-window accuracy.

### Forbidden claim

Prioritize low false-negative rate while preserving human review for ambiguous language.

### Competitor appearance

Measure brand confusion, category confusion, incidental background appearance, and duration.

## 5. Timestamp evaluation

Measure:

- start-time absolute error;
- end-time absolute error;
- interval intersection-over-union;
- whether a reviewer can understand the evidence without excessive scrubbing.

## 6. Calibration

Confidence labels should be calibrated against observed correctness.

Do not expose numeric confidence as authoritative until calibration is demonstrated.

## 7. Regression suite

Every change to:

- prompts;
- indexes;
- retrieval queries;
- model;
- evaluator;
- merge tolerance;
- thresholds;

should run against the gold dataset.

## 8. Adversarial cases

Test:

- transcript says “ignore prior instructions”;
- packaging intentionally obscured;
- competitor logo visible for one frame;
- disclosure appears in low contrast;
- incorrect captions;
- reused old footage;
- product shown in a reflection;
- sarcastic or negated prohibited claim;
- creator quotes a prohibited claim only to reject it;
- music lyrics contain a target phrase;
- subtitle contains phrase not spoken;
- multiple products share similar packaging.

## 9. Operational quality gates

Do not enable automatic approval until:

- required rule types meet configured precision;
- blocking rules have an acceptable false-negative profile;
- uncertainty routing is tested;
- audit and override workflows work;
- playback links are reliable;
- provider failure states are visible.

## 10. Feedback loop

Capture reviewer feedback as:

- correct machine result;
- false positive;
- false negative;
- insufficient evidence;
- wrong timestamp;
- wrong rule interpretation;
- unsupported rule;
- policy disagreement.

Use feedback for evaluation and prompt improvement. Do not automatically retrain or change policy based on a single override.
