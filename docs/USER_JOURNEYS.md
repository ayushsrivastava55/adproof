# User Journeys

## Journey 1: Create a campaign from a brief

1. Campaign manager creates a campaign.
2. Manager adds brand, product, channels, dates, and brief.
3. System extracts candidate requirements.
4. Each candidate requirement shows the source sentence.
5. Manager edits rule type, threshold, severity, and reviewer guidance.
6. Subjective requirements are marked for human review.
7. Manager confirms the active rule set.
8. System records rule-set version one.

### Acceptance criteria

- No extracted rule becomes active without confirmation.
- Every rule links back to the source brief.
- Unsupported requirements are visibly flagged.
- Contradictions are surfaced before activation.

## Journey 2: Submit a creator video

1. User selects campaign.
2. User enters creator and submission metadata.
3. User provides an authorized media file or source.
4. System validates media accessibility and format.
5. System creates a submission version.
6. Media ingestion begins.
7. User sees real processing status.
8. On failure, user sees reason and recovery action.

### Acceptance criteria

- Duplicate submission attempts are handled idempotently.
- The original media source is preserved.
- The interface never shows “analysis complete” before all required jobs reach a valid terminal state.

## Journey 3: Review an analyzed submission

1. Reviewer opens the submission report.
2. Reviewer sees overall status and unresolved issues.
3. Reviewer opens a failed or uncertain rule.
4. Video seeks to the strongest evidence.
5. Reviewer can inspect all supporting and conflicting moments.
6. Reviewer confirms, overrides, or escalates the rule.
7. Reviewer records a reason when overriding.
8. Final submission decision becomes available when policy requirements are met.

### Acceptance criteria

- Evidence opens at the correct timestamp.
- Evidence provenance is visible.
- Overrides require a reason.
- Human and automated conclusions remain distinguishable.

## Journey 4: Request creator changes

1. Reviewer selects failed or uncertain requirements.
2. System drafts revision instructions grounded in the rule and evidence.
3. Reviewer edits and approves the message.
4. Submission enters changes-requested state.
5. Creator provides a new version.
6. New version is evaluated against the same active rule-set version unless explicitly changed.

### Acceptance criteria

- Prior versions remain accessible.
- Revision instructions do not claim facts unsupported by evidence.
- The report compares the new version with prior failures.

## Journey 5: Investigate campaign trends

1. Campaign manager opens campaign analytics.
2. Manager sees repeated failure patterns.
3. Manager filters by rule, creator, channel, or reviewer.
4. Manager opens representative evidence.
5. Manager updates future templates or creator guidance.

### Acceptance criteria

- Aggregates link to underlying submissions.
- Analytics distinguish machine results from final human outcomes.
- Metrics exclude errored or incomplete evaluations unless explicitly selected.

## Journey 6: Resolve a dispute

1. Authorized user opens the submission audit record.
2. User sees original brief, confirmed rules, media version, machine findings, reviewer actions, and timestamps.
3. User exports or shares an evidence package.
4. Final resolution is recorded without deleting prior decisions.

### Acceptance criteria

- History is append-only for material decisions.
- Export clearly identifies generated content and human decisions.
- Evidence links remain usable according to retention policy.
