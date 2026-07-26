# Representative Product Dataset Specification

This dataset exists to exercise product behavior consistently during development, evaluation, sales demonstrations, and quality reviews.

It must always be clearly identified as representative data.

## Fictional brand

Brand: PulseBar

Product: PulseBar Protein Bar

Approved attributes:

- 20 grams of protein;
- chocolate almond flavour;
- individually wrapped;
- code AYUSH20.

Prohibited claims:

- guaranteed weight loss;
- cures fatigue;
- medically proven to build muscle;
- zero health risks.

Competitor category:

- packaged protein and nutrition bars.

## Campaign requirements

1. PulseBar package must be clearly visible for at least six seconds.
2. Creator must say “20 grams of protein” or an approved equivalent.
3. Creator must state code AYUSH20.
4. An advertising disclosure must be spoken or clearly visible.
5. Creator must open or consume the product.
6. No competing protein bar should be actively displayed.
7. No prohibited health or weight-loss claim may be made.

## Submission A: Meets requirements

Expected characteristics:

- package clearly shown;
- approved protein statement;
- correct code;
- clear disclosure;
- product opened and consumed;
- no competitor;
- no prohibited claims.

Expected recommendation:

- machine recommendation may be approve, subject to configured human-review policy.

## Submission B: Missing disclosure

Expected characteristics:

- product visible;
- correct talking points;
- correct code;
- product demonstrated;
- no clear disclosure.

Expected recommendation:

- changes requested or manual review according to disclosure policy.

## Submission C: Blocking violation

Expected characteristics:

- product visible;
- prohibited “guaranteed weight loss” claim;
- competitor actively displayed.

Expected recommendation:

- reject or escalate according to policy.

## Submission D: Ambiguous low-quality media

Expected characteristics:

- dim video;
- product partly obscured;
- noisy speech;
- possible disclosure unreadable;
- uncertain brand identification.

Expected recommendation:

- human review required, not an automatic failure.

## Expected evidence annotations

For every representative submission, maintain human annotations for:

- product visibility ranges;
- disclosure range;
- spoken claims;
- code mention;
- product-use event;
- competitor appearance;
- prohibited claim;
- ambiguity notes.

## Integrity rule

Representative expected results must not be inserted into a live report unless the user explicitly selected representative mode. Live processing must use actual provider outputs.
