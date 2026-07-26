# VideoDB Integration Specification

## 1. Purpose

VideoDB is the primary media infrastructure for:

- video ingestion;
- video organization through collections;
- spoken-word indexing;
- scene or visual indexing;
- multimodal search;
- timestamped retrieval;
- playable streams;
- evidence clips and composed outputs where supported.

Current behavior must be verified against official documentation before implementation.

## 2. Official documentation

- Documentation home: https://docs.videodb.io/
- Documentation index: https://docs.videodb.io/llms.txt
- Quickstart: https://docs.videodb.io/pages/getting-started/quickstart
- Data model: https://docs.videodb.io/pages/core-concepts/data-model
- Create an index: https://docs.videodb.io/pages/understand/indexing-pipelines/create-an-index
- Multimodal search: https://docs.videodb.io/examples-and-tutorials/video-rag/multimodal-search
- Collection search: https://docs.videodb.io/pages/understand/search-and-retrieval/collection-search
- API reference: https://docs.videodb.io/api-reference/introduction
- Collections API: https://docs.videodb.io/api-reference/collections
- Upload to collection: https://docs.videodb.io/api-reference/collections/upload_to_collection
- Editor timeline compilation: https://docs.videodb.io/api-reference/editor/compile_editor_timeline

Use the documentation index to locate the newest relevant page. Do not rely only on examples copied into this repository.

## 3. Internal adapter contract

The integration should expose internal operations conceptually equivalent to:

- create or resolve collection;
- ingest video;
- obtain video metadata;
- obtain playable stream;
- request spoken-word indexing;
- request focused visual indexing;
- inspect index status;
- search one video;
- search a collection;
- normalize search results;
- create evidence clip;
- compose evidence reel;
- inspect asynchronous job status.

Names above are internal product operations, not claims about exact SDK method names.

## 4. Collection strategy

Recommended initial strategy:

- one logical VideoDB collection per workspace or per data-isolation boundary;
- campaign ID stored in provider metadata where supported;
- application database remains the source of truth for authorization and relationships;
- collection-wide search used only with explicit workspace and campaign filters.

A collection-per-campaign strategy may simplify isolation but can increase operational overhead. Confirm current limits and pricing before selecting.

## 5. Video ingestion

Store the following after ingestion:

- internal media ID;
- provider video ID;
- provider collection ID;
- source type;
- source URL or upload reference;
- original file name;
- duration where available;
- ingestion status;
- stream reference;
- metadata;
- provider response version or snapshot;
- error details;
- timestamps.

Never display a successful submission report merely because the upload request was accepted. Ingestion and indexing are separate states.

## 6. Spoken-word indexing

Use spoken indexing for:

- brand mentions;
- required phrases;
- call-to-action language;
- discount codes;
- disclosure language;
- prohibited claims;
- competitor mentions;
- price statements;
- product attributes.

Consider:

- expected language;
- code-switching;
- pronunciation variants;
- captions versus speech;
- noisy audio;
- music;
- overlapping speakers;
- text normalization;
- exact phrase versus semantic meaning.

## 7. Focused visual indexing

Avoid one universal visual prompt.

Create distinct indexes or extraction configurations for important domains.

### Product presence index

Capture:

- whether target product is visible;
- brand or product identity;
- packaging side;
- logo readability;
- screen position;
- approximate prominence;
- obstruction;
- active handling;
- confidence or uncertainty.

### Disclosure index

Capture:

- visible disclosure text;
- wording;
- time interval;
- placement;
- readability;
- contrast;
- duration.

### Competitor index

Capture:

- competing product category;
- readable brand;
- ambiguous packaging;
- background versus active use;
- duration.

### Product-use index

Capture:

- opening;
- holding;
- applying;
- consuming;
- demonstrating;
- unsafe or prohibited use.

### On-screen claim index

Capture:

- visible claims;
- prices;
- percentages;
- guarantees;
- health language;
- comparison language.

## 8. Search strategy

For each rule, retrieval should include:

- supporting query;
- alternative supporting query;
- conflicting query where relevant;
- exact keyword search where applicable;
- semantic search where applicable;
- modality;
- relevant index;
- time-window constraint;
- result limit;
- metadata filters.

Search should be repeatable and versioned. Persist the query and retrieval configuration used for a report.

## 9. Result normalization

Normalize provider output into an internal evidence representation containing:

- video reference;
- start time;
- end time;
- text or scene description;
- retrieval score where available;
- modality;
- provider index reference;
- retrieval query;
- provider result reference;
- playback reference;
- raw response reference or trace ID.

## 10. Asynchronous behavior

Indexing and media composition may be asynchronous.

The system must:

- persist provider job references;
- poll or consume callbacks where supported;
- use bounded retries;
- avoid duplicate jobs;
- surface queued and running states;
- mark terminal failures;
- allow manual retry;
- avoid infinite polling;
- record elapsed time.

## 11. Empty-result policy

An empty result is not automatically a failure.

Classify it as one of:

- likely absent;
- low-confidence absence;
- index incomplete;
- query insufficient;
- unsupported modality;
- media quality issue;
- provider failure.

The rule’s configured policy determines whether absence can produce a machine fail or requires review.

## 12. Playback and evidence links

Evidence links must:

- open the correct media;
- seek to the correct timestamp;
- respect authorization;
- remain valid for the intended review window;
- avoid exposing cross-workspace media;
- handle expired provider URLs.

## 13. Evidence reels

An evidence reel may contain:

- strongest supporting moment per required rule;
- strongest violating moment per blocking rule;
- short context before and after each moment;
- text labels identifying the associated requirement;
- separation between supporting and conflicting evidence.

The reel is a review aid, not the authoritative evidence record.

## 14. Integration verification checklist

Before calling the integration operational:

- real credentials configured;
- real upload succeeds;
- playable stream succeeds;
- spoken index completes;
- visual index completes;
- spoken search returns timestamped results;
- visual search returns timestamped results;
- collection filtering is validated;
- error state is tested;
- empty-result state is tested;
- expired playback behavior is tested;
- asynchronous retry behavior is tested;
- provider IDs are persisted;
- no fixture data appears in a live report.
