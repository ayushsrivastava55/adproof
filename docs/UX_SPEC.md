# User Experience Specification

## 1. Experience goal

A reviewer should be able to understand the submission’s state, inspect the strongest evidence, and make a defensible decision without manually scrubbing the full video.

## 2. Navigation

Primary sections:

- Overview;
- Campaigns;
- Submissions;
- Review Queue;
- Creators;
- Analytics;
- Templates;
- Settings.

## 3. Campaign list

Show:

- campaign name;
- brand;
- channel;
- active dates;
- submission counts;
- review queue;
- approval rate;
- processing errors;
- current rule-set version.

## 4. Campaign setup

Sections:

- basics;
- campaign brief;
- reference assets;
- generated requirements;
- review policy;
- submission settings.

### Rule editor

Each rule card shows:

- requirement;
- source brief text;
- rule type;
- modality;
- threshold;
- severity;
- automatable status;
- uncertainty;
- reviewer instructions.

## 5. Submission detail

### Header

- creator;
- campaign;
- submission version;
- processing status;
- final status;
- assigned reviewer;
- dates.

### Video workspace

- primary video player;
- evidence markers;
- seek controls;
- captions where available;
- playback speed;
- evidence context expansion.

### Rule list

Each row shows:

- status icon;
- requirement;
- measured result;
- confidence label;
- evidence count;
- reviewer state.

### Evidence panel

Show:

- strongest supporting evidence;
- conflicting evidence;
- timestamp;
- modality;
- retrieval explanation;
- provenance;
- context before and after;
- play action.

## 6. Status language

Use precise language.

Prefer:

- “No matching evidence found”
- “Visual index still processing”
- “Low-confidence result”
- “Human review required”
- “Machine recommendation”
- “Reviewer-confirmed”

Avoid:

- “Definitely absent”
- “Fully compliant” before review policy is satisfied
- “AI approved”
- “Guaranteed violation”
- “Complete” while any required process is unresolved

## 7. Uncertainty design

Uncertainty should be prominent, actionable, and non-alarming.

Show:

- why uncertain;
- what is missing;
- whether reprocessing may help;
- whether manual review is required;
- relevant media context.

## 8. Review actions

Available actions:

- confirm;
- override;
- request changes;
- escalate;
- approve submission;
- reject submission;
- add note;
- assign reviewer.

An override requires a reason.

## 9. Processing states

The interface should separately represent:

- upload;
- ingestion;
- spoken indexing;
- visual indexing;
- retrieval;
- evaluation;
- report generation;
- evidence reel generation.

A single generic spinner hides useful information and makes failures difficult to resolve.

## 10. Analytics

Campaign analytics should show:

- review-ready submissions;
- unresolved submissions;
- common failed rules;
- time to review;
- resubmission rate;
- override rate;
- processing errors.

Every aggregate should link back to submissions.

## 11. Empty states

Empty states should explain the next action.

Examples:

- no campaigns: create or import a campaign;
- no submissions: share submission link or upload media;
- no evidence: review index status and inspect full video;
- no analytics: process and review submissions first.

## 12. Accessibility

- keyboard-accessible player controls;
- readable status labels beyond color;
- caption support;
- sufficient contrast;
- focus visibility;
- timestamps readable by assistive technology;
- no autoplay with sound;
- clear error announcements.

## 13. Mobile posture

Campaign setup and deep review are desktop-first. Mobile should support status checks, evidence playback, comments, and lightweight approval where policy permits.
