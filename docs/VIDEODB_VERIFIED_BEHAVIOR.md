# VideoDB Verified Behavior

Records what was **verified**, what remains **assumed**, and how each was
established. Separates confirmed facts from assumptions, per the documentation
rules.

- Verification date: 2026-07-26
- Pinned SDK: `videodb==0.5.1` (current PyPI release at time of verification)
- Method: official documentation, reconciled against **source introspection of
  the pinned SDK**, which is authoritative where the two disagree.

## 1. Documentation pages consulted

| Page | URL |
| --- | --- |
| Documentation index | https://docs.videodb.io/llms.txt |
| Quickstart | https://docs.videodb.io/pages/getting-started/quickstart |
| Python SDK | https://docs.videodb.io/pages/getting-started/python |
| Data model | https://docs.videodb.io/pages/core-concepts/data-model |
| Indexes and search | https://docs.videodb.io/pages/core-concepts/indexes-and-search |
| Create scene index | https://docs.videodb.io/api-reference/videos/indexing/create_scene_index |
| Search video index | https://docs.videodb.io/api-reference/videos/indexing/search_video_index |
| Generate video stream | https://docs.videodb.io/api-reference/videos/streaming/generate_video_stream |
| Search V2 | https://docs.videodb.io/api-reference/search-v2/ |

## 2. A documentation conflict, and how it was resolved

Three current pages describe three different SDK surfaces for the same
operations:

| Page | Spoken index | Visual index | Search |
| --- | --- | --- | --- |
| Python SDK | `index_spoken_words()` | not shown | `search()` → `results.shots` |
| Quickstart | `index_audio()` | `index_visuals()` | `search()` |
| Indexes and search | `understand()` then `index(name=, source=)` | same | `search` / `semantic_search` / `query` / `aggregate` |

**Resolution:** all of these exist on `videodb==0.5.1`; they are overlapping
generations, not alternatives to choose between blindly. AdProof uses the
surface that lets a rule name its exact index and search mode, because
VIDEODB_INTEGRATION.md s8 requires retrieval to be reproducible and versioned.

`ask()` exists and is deliberately **not** used: it is a chat-over-video
affordance and falls outside this product's boundary.

## 3. Confirmed facts

Each was confirmed by reading the pinned SDK source. `app/tests/test_integrity.py`
asserts every one of them at test time, so a version bump that breaks any of
them fails the build rather than failing silently in production.

| # | Fact | Source |
| --- | --- | --- |
| C-1 | `Collection.upload(source=, media_type=, name=, file_path=, url=, callback_url=)` returns `Video \| Audio \| Image \| None` | `collection.py` |
| C-2 | `Video.index_spoken_words(language_code=, segmentation_type=, force=, callback_url=)` returns `None` | `video.py` |
| C-3 | `Video.index_scenes(extraction_type=, extraction_config=, prompt=, metadata=, model_name=, name=, scenes=, callback_url=, sandbox_id=)` returns `scene_index_id: str` | `video.py` |
| C-4 | `SceneExtractionType` = `shot_based="shot"`, `time_based="time"`, `transcript="transcript"` | `_constants.py` |
| C-5 | Time-based `extraction_config` keys: `time`, `frame_count`, `select_frames` | `video.py` docstring |
| C-6 | `Video.legacy_search(query, search_type, index_type, result_threshold, score_threshold, dynamic_score_percentage, filter, **kwargs)`; `scene_index_id` passes through `**kwargs` into the request body | `video.py`, `search.py` |
| C-7 | `IndexType` = `spoken_word`, `scene`; `SearchType` = `semantic`, `keyword` | `_constants.py` |
| C-8 | Keyword search is implemented for a single video but **raises `NotImplementedError` for collection search** | `search.py` `KeywordSearch.search_inside_collection` |
| C-9 | Result shape: `SearchResult.shots[] → Shot(video_id, video_length, video_title, start, end, text, search_score, scene_index_id, scene_index_name, metadata, stream_url, player_url)`; `search_score` is populated from the response field `score` | `shot.py`, `search.py` |
| C-10 | `Video.generate_stream(timeline=None)` returns a stream URL string | `video.py` |
| C-11 | Default search thresholds: `result_threshold=5`, `score_threshold=0.2` | `_constants.py` `SemanticSearchDefaultValues` |
| C-12 | `Video.get_transcript(segmenter="word"\|"time", ...)` exists; word-level transcript is available | `video.py` |

### C-13: indexing is blocking, not pollable — the load-bearing finding

`_http_client._parse_response` inspects the response. When
`status == "processing"` and `request_type == "sync"`, it calls `_get_output()`,
which polls the returned `output_url` every `poll_interval` (default **5s**)
until status leaves `{"processing", "in progress"}`, or `max_poll_time`
(default **500s**) elapses, at which point it raises `RequestTimeoutError`.

Consequences, all of which shaped the implementation:

1. There is **no public SDK method to poll an index job by id**. AdProof
   therefore derives per-stage async state from its own `processing_job` rows,
   and the UI says so explicitly rather than implying it polls VideoDB.
2. No progress percentage is available to AdProof, so none is displayed.
3. `index_spoken_words()` calls `post(..., show_progress=True)` **without**
   forwarding `max_poll_time`/`poll_interval`, so its polling budget cannot be
   overridden. A long video can raise `RequestTimeoutError`.
4. A timeout does **not** mean the provider-side job failed. It is mapped to
   `ProviderTimeout` (retryable) and its user-facing message says the index may
   still be building, rather than reporting a failure that did not happen.
5. Blocking calls must run in a worker, never in a request.

## 3a. Live account verification — COMPLETE (2026-07-26)

Verified against a funded account using a 555s public-domain video
(`archive.org/download/DuckandC1951`), then the **full AdProof slice** was run
against the live provider with no scripted adapter at any point.

### Confirmed provider behaviour

| # | Fact | Observed |
| --- | --- | --- |
| L-1 | Upload returns a video id and a duration | `m-z-019f9af0-...`, `length` 555.0 (**A-8 resolved: duration IS reported**) |
| L-2 | Stream URL format | `https://play.videodb.io/v1/<uuid>.m3u8`, plus a `player_url` |
| L-3 | Spoken indexing is blocking, shows a progress bar, returns `None` | 21.5s for 555s of audio (**A-1 confirmed**) |
| L-4 | Scene index returns an addressable id | `b06a16a2404ec127` (**A-2 confirmed**) |
| L-5 | `scene_index_id` scopes search to that index | hits returned `scene_index_id` and `scene_index_name` matching the requested index (**A-1b confirmed**) |
| L-6 | Keyword spoken search performs literal matching | 22 exact hits for `"duck and cover"` (**A-3 confirmed**) |
| L-7 | Shots carry `start` AND `end` | every hit, e.g. `21.56 -> 23.44` (**A-5 confirmed**) |
| L-8 | Shots carry a score | every hit (**A-6 confirmed**) |
| L-9 | `video_length` arrives as a **string** in the shot payload | `"555"`, not `555.0` |

### C-14: an empty search is RAISED, not returned — highest-impact finding

VideoDB signals "nothing matched" by raising
`InvalidRequestError("Invalid request: No results found.")`. It does **not**
return an empty result set.

Mapping that to a provider failure would report a legitimate absence as an
error, collapsing the distinction between *"we looked and found nothing"* and
*"we never looked"* — the exact confusion this product exists to prevent. The
adapter now returns `[]` for this case and lets the caller classify the absence
under the rule's policy.

### C-15: scene indexing is asynchronous despite returning an id

`index_scenes()` returned a `scene_index_id` in **3.1 seconds** for a 555s
video. Searches against that id then failed with "No results found" for several
minutes, before eventually returning 4 hits with 56 scene records present
(555 / 10 ≈ 56 ✓).

So a returned id means *accepted*, not *queryable*. AdProof now polls
`get_scene_index()` until records exist before marking the index job succeeded,
and records the confirmed `record_count` on the index. Without that wait, every
fast-returning visual index would have produced an empty search that was
misclassified as "not visible".

### C-16: `time` in extraction_config must be a positive integer

A float is rejected: `'time' in the extraction_config must be a positive
integer`. The production default was `2.0`, so **every visual index would have
failed**. Now `int`, validated in the adapter before the call.

### C-17: score semantics differ by search type — do not compare them

| Search | Observed scores |
| --- | --- |
| spoken keyword | exactly `1.0` on every hit |
| spoken semantic | `0.60`–`0.62` |
| scene semantic | `0.47`–`0.69` |

The keyword score is a **match flag, not a relevance measure**. This is direct
evidence for VERIFICATION_ENGINE.md s7's prohibition on collapsing unrelated
confidence signals into one number, and for why bands are derived per index and
never compared across index types.

### C-18: semantic hits span whole blocks, not phrase boundaries

A semantic spoken hit covered `316.01 -> 358.46` (**42 seconds**) — an entire
transcript block containing the phrase. A scene semantic hit covered
`170.17 -> 220.22` (**50s**, five consecutive 10s samples merged by the
provider).

Consequence: counting semantic hits toward a measurement would massively
overstate duration and occurrence. The decision to mark semantic runs
`counts_toward_measurement=False` is validated by observation, not just theory.

### Full-slice run against the live provider

Three rules, real indexes, real searches, real evidence:

| Rule | Result | Measured |
| --- | --- | --- |
| Narrator says "duck and cover" | `pass` | 22 occurrences (threshold 1), 23 evidence items |
| Classroom visible >= 15s | `pass` | 100.10s across 23 merged intervals, resolution 2.0s, confidence `medium` |
| Narrator says "quantum entanglement" | `fail` | 0 occurrences, absence `likely_absent`, 0 evidence |

Playback resolved to the real stream seeking to `21.56s`. The visual index built
by the pipeline (`447ef80fa72e9a5a`) used the production 2s sampling, and the
merged intervals align to 2s boundaries as expected.

**Slice acceptance criteria 1-6 are met.**

## 3b. Cross-check against the official `video-db/skills` package (2026-07-26)

Installed via `npx skills add video-db/skills` and reviewed before use. Security
scanners disagreed (Socket: 0 alerts; Snyk: Critical Risk); the payload is 22
markdown reference files plus one websocket listener script, which was read and
contains no shell execution or data exfiltration. Treated as reference material,
not as instructions.

### Confirmed my implementation

| Vendor statement | Bearing on AdProof |
| --- | --- |
| "Nothing was removed. Every v1 method still exists and works in 0.5.0, and none raise `DeprecationWarning`." | `legacy_search` is a supported choice, not deprecated debt |
| "legacy search raises `InvalidRequestError` when no results match... treat `'No results found'` as an empty result set" | **Independently confirms C-14.** My fix matches the vendor's documented pattern; A-11 is now documented, not merely observed |
| "Use `SearchType.semantic` with `index_type=IndexType.scene` — most reliable, works on all plans. `SearchType.scene` may not be available on Free tier" | The combination AdProof uses is the recommended one |
| "Collection-level search only supports `SearchType.semantic`" | Matches C-8 |
| `scene_index_id` targets a specific index; multiple scene indexes per video are supported | Confirms the focused-index strategy |
| Explicit `legacy_search()` avoids v0.5.0's silent v1/v2 routing changes | AdProof calls `legacy_search()` explicitly, so it is unaffected by the routing sharp edge |

### Changed my implementation

1. **Relevance cutoff raised 0.2 -> 0.3.** Vendor recommends `score_threshold >= 0.3`
   for scene semantic search to filter low-relevance noise. Measured impact on the
   10s index: `0.2 -> 100.1s` raw span (lowest score 0.217), `0.3 -> 70.1s` (0.486),
   `0.5 -> 40.0s` (0.628). **A 30% swing in measured duration from a threshold
   alone.** The cutoff is now surfaced in the report UI next to the measurement,
   because hiding a parameter that moves the number that much would be dishonest.
2. **Existing-index recovery added.** `index_scenes()` has no `force`; the
   vendor-documented v1 recovery is to read the existing id back out of the error
   message. Added, recovering a real provider id rather than inventing one.

### C-19: observed behaviour CONTRADICTS the vendor documentation

The vendor states `index_scenes()` "raises an error if a scene index already
exists". **It does not.** Re-requesting an index with an identical `name`,
prompt, and config created a **second, distinct index** (`b06a16a2404ec127` and
`ebde0855afdcd1d8`, both named `adproof_spike_visual`).

Consequence: nothing on the provider side prevents duplicate index creation, so
a retry with a missing local record would silently double VLM cost and create
ambiguity about which index backs the evidence. AdProof's own `MediaIndex`
dedupe is therefore **load-bearing, not belt-and-braces**. The duplicate created
during this probe was deleted.

### C-20: scene indexes DO expose a status — correcting C-13

`list_scene_index()` returns
`{'metadata', 'name', 'scene_index_id', 'status'}` with `status: 'done'`
observed on a completed index.

This narrows C-13, which claimed no pollable index status exists. Accurate
statement: **spoken-word indexing has no status surface; scene indexes do.**
The readiness gate now requires the provider's own `status == 'done'` *and* a
non-zero record count, since status alone was seen reading `done` while searches
still could not reach the index. Only `'done'` is treated as ready; no other
status value is assumed, because inventing status values is precisely what the
integration rules forbid.

## 3c. Processing limits and the retrieval-cap defect (2026-07-26)

VideoDB documents no maximum video duration. Every binding limit was AdProof's
own.

### C-21: a fixed retrieval cap silently truncated the measurement

`result_threshold` was hard-coded to 50. On the 555s test video that produced a
**3x undercount presented as a measurement**:

| result_threshold | hits | measured visible duration |
| --- | --- | --- |
| 10 | 8 | 20.0s |
| **50 (old default)** | **23** | **100.1s** |
| 200 | 42 | **315.5s** |
| 1000 | 42 | 315.5s (saturated -- true value) |

The earlier live runs reported 100.1s. The correct figure is 315.5s. Keyword
spoken search was unaffected (22 hits at every threshold).

Two fixes, because a larger constant only moves the cliff:

1. The cap is now derived from the media's own duration -- roughly two per
   sampling interval -- so it does not bind in practice.
2. **Saturation is detected.** When a run returns exactly as many results as
   requested, `result_truncated` is recorded. Truncation can only *understate*,
   so a threshold already met remains a valid `pass`, but a shortfall can no
   longer be reported as `fail`; it becomes `uncertain` and says the
   measurement may understate the truth. Surfaced in the report UI.

### Practical duration ceilings

| Limit | Value | Binds at |
| --- | --- | --- |
| SDK `max_poll_time` on blocking calls (upload, spoken index); **not overridable** for `index_spoken_words` | 500s | Observed spoken indexing ran ~26x realtime (555s audio in 21.5s), so ~3.5h of media before the budget is at risk |
| `wait_for_scene_index` timeout | 600s per attempt, 3 attempts | Visual indexing is the slowest stage; long media may need this raised |
| Visual indexing cost/time | 1 VLM call per 2s of video | 555s -> 275 records. **Scales linearly and dominates cost.** A 1h video is ~1800 frames |
| Retrieval cap | duration-derived, ceiling 5000 | ~2.7h at 2s sampling before the ceiling could bind; saturation is detected regardless |

**Practical guidance:** short-form creator content (under ~10 minutes), which is
the product's actual target, is comfortably inside every limit. Beyond roughly
30 minutes, visual indexing cost and the 600s readiness wait become the real
constraints, and the sampling interval should be raised. Nothing has been tested
above 555s (9m15s).

## 4. Open assumptions

Resolved by live verification (see s3a): **A-1, A-2, A-3, A-5, A-6, A-8, A-9**.

Still open:

| # | Assumption | Risk if wrong | Containment |
| --- | --- | --- | --- |
| A-4 | An already-indexed video raises `InvalidRequestError` whose message contains "already indexed"/"already exists" | A benign repeat is misreported as a failure | Not yet observed. Text matching is narrow; anything unmatched becomes a terminal failure, the safe direction |
| A-7 | Stream URLs are long-lived | Evidence links break mid-review | Playback goes through `/api/evidence/{id}/playback`, so URL handling can change in one place |
| A-10 | Stream URLs are not access controlled | Cross-tenant media leak | **Assumed true (unsafe).** `play.videodb.io/v1/<uuid>.m3u8` carries no visible auth. Disclosed in `/api/integrity` and the UI. Phase 5 must fix this before real creator media |
| A-11 | "No results found" is the only phrasing VideoDB uses for an empty result | A different phrasing would resurface as a provider failure | Conservative: an unmatched message becomes `error`, never silent absence. Two variants matched |
| A-12 | Spoken indexing is fully queryable when `index_spoken_words()` returns | Spoken search could run against a partial index | Observed working immediately in the live run, but **not proven** the way C-15 was for scene indexes. No readiness check exists for the spoken index |
| A-13 | Collection metadata filtering behaves as documented | Workspace/campaign scoping via provider metadata may not work | Not needed by this slice, which searches per video and scopes in the database |

## 5. Not used, and why

| Capability | Reason |
| --- | --- |
| `ask()`, `search()` (v2 high-level) | Chat/RAG affordances; outside the product boundary |
| `query()`, `aggregate()` | Require v2 queryable indexes; not needed by the two Phase 1 rule types |
| `extract_scenes()` / `SceneCollection` | `index_scenes` covers the need; a second scene concept would add ambiguity |
| RTStream, generation, dubbing, reframe | Not part of verification |
| Collection-level search | Keyword search is not implemented for collections (C-8); per-video search is sufficient and better scoped |
