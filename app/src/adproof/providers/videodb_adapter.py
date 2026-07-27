"""The only module permitted to import the VideoDB SDK.

Every method used here was verified by source introspection of videodb==0.5.1.
See docs/VIDEODB_VERIFIED_BEHAVIOR.md for the evidence behind each call.

Two rules govern this file:
  1. No method, parameter, field, or status value is used unless it was
     verified to exist in the pinned SDK.
  2. No failure is converted into a success-shaped return value.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import videodb
from videodb import IndexType, SceneExtractionType, SearchType
from videodb.exceptions import (
    AuthenticationError,
    InvalidRequestError,
    RequestTimeoutError,
    SearchError,
    VideodbError,
)

from .. import config
from ..config import ConfigurationError
from .errors import (
    ProviderContractViolation,
    ProviderNotConfigured,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnavailable,
)
from .types import CreatedIndex, IngestedMedia, RetrievedShot

logger = logging.getLogger(__name__)

#: Pinned SDK version, recorded onto every media asset for provenance.
SDK_VERSION = "videodb==0.5.1"

#: Substring the SDK/provider uses when an index already exists. Matching on
#: message text is fragile; see ASSUMPTION A-4. When it does not match, the
#: error is surfaced as a terminal failure rather than assumed benign.
_ALREADY_INDEXED_MARKERS = ("already indexed", "already exists")

#: VERIFIED 2026-07-26 against the live API: VideoDB signals an empty search by
#: RAISING InvalidRequestError("No results found."), not by returning an empty
#: result set. Treating that as a failure would report a legitimate absence as a
#: provider error, which is precisely the confusion this product must not make.
#: Matching on message text is fragile (see ASSUMPTION A-11); anything that does
#: not match is still surfaced as a genuine failure, which is the safe direction.
_EMPTY_RESULT_MARKERS = ("no results found", "no result found")

#: The only scene-index status value observed from list_scene_index(). No other
#: value is assumed to mean "ready" -- inventing status values is exactly what
#: the integration rules forbid.
_SCENE_INDEX_READY = "done"

#: Substrings that indicate a provider-side terminal failure in a status value.
#: Conservative: an unrecognised status means "keep waiting", not "succeeded".
_SCENE_INDEX_FAILURE_MARKERS = ("fail", "error", "cancel")


def _looks_like_failure(status: str) -> bool:
    lowered = str(status).lower()
    return any(marker in lowered for marker in _SCENE_INDEX_FAILURE_MARKERS)


#: Longest provider message echoed into a user-facing summary.
_MAX_PROVIDER_MESSAGE = 300


def _reason(exc: Exception) -> str:
    """The provider's own explanation, for the user-facing summary.

    A generic "VideoDB rejected the request" tells a reviewer nothing they can
    act on. The provider's text usually names the actual cause (quota, credit,
    unsupported format), so it is echoed verbatim rather than paraphrased --
    paraphrasing risks asserting a cause we did not observe.

    The SDK raises these from HTTP error bodies, which carry no credential; the
    API key travels in a request header and is never echoed back.
    """
    message = str(exc).strip()
    prefix = "invalid request: "
    if message.lower().startswith(prefix):
        message = message[len(prefix):].strip()
    if len(message) > _MAX_PROVIDER_MESSAGE:
        message = message[:_MAX_PROVIDER_MESSAGE] + "..."
    return message


#: Vendor-documented pattern for recovering an existing scene index id from the
#: "already exists" error raised by index_scenes(), which has no `force` option.
#: Source: video-db/skills reference/legacy/search.md ("Idempotent indexing").
_EXISTING_INDEX_ID_RE = re.compile(r"id\s+([a-f0-9]{6,})", re.IGNORECASE)


def _existing_scene_index_id(exc: Exception) -> str | None:
    """Extract a pre-existing scene index id from a provider error message.

    Returns None when the message carries no id, so the caller raises rather
    than guessing at an index that may not exist.
    """
    match = _EXISTING_INDEX_ID_RE.search(str(exc))
    return match.group(1) if match else None


def _shot_snapshot(shot: Any) -> dict[str, Any]:
    """Verbatim copy of the provider's shot, minus the live connection."""
    return {
        key: value
        for key, value in vars(shot).items()
        if not key.startswith("_")
    }


class VideoDBAdapter:
    """Internal operations over VideoDB.

    Callers receive internal types (providers.types) and internal errors
    (providers.errors) only.
    """

    def __init__(self, api_key: str | None = None) -> None:
        try:
            # Read through the module so runtime configuration changes and
            # tests both see the current settings object.
            self._api_key = api_key or config.settings.require_videodb_api_key()
        except ConfigurationError as exc:
            raise ProviderNotConfigured(str(exc)) from exc
        self._connection = None

    # -- connection -------------------------------------------------------

    def _conn(self):
        if self._connection is None:
            try:
                self._connection = videodb.connect(api_key=self._api_key)
            except AuthenticationError as exc:
                raise ProviderRejected(
                    f"VideoDB rejected the configured credential: {_reason(exc)}",
                    str(exc),
                ) from exc
            except VideodbError as exc:
                raise ProviderUnavailable(
                    "Could not connect to VideoDB.", str(exc)
                ) from exc
        return self._connection

    def resolve_collection(self, collection_id: str | None = None):
        """Resolve the workspace's collection.

        Defaults to the account's "default" collection, which is what
        Connection.get_collection() returns with no argument.
        """
        try:
            if collection_id:
                return self._conn().get_collection(collection_id)
            return self._conn().get_collection()
        except InvalidRequestError as exc:
            raise ProviderRejected(
                f"VideoDB rejected collection lookup for {collection_id!r}.", str(exc)
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable(
                "Could not resolve the VideoDB collection.", str(exc)
            ) from exc

    # -- ingestion --------------------------------------------------------

    def ingest(
        self,
        *,
        source_url: str | None = None,
        source_file_path: str | None = None,
        name: str | None = None,
        collection_id: str | None = None,
    ) -> IngestedMedia:
        """Upload media and return its normalized reference.

        Blocking: Collection.upload() goes through the SDK's synchronous
        polling path.
        """
        if bool(source_url) == bool(source_file_path):
            raise ProviderRejected(
                "Exactly one of source_url or source_file_path must be provided."
            )

        collection = self.resolve_collection(collection_id)
        try:
            if source_url:
                media = collection.upload(url=source_url, name=name)
            else:
                media = collection.upload(file_path=source_file_path, name=name)
        except RequestTimeoutError as exc:
            raise ProviderTimeout(
                "VideoDB upload did not complete within the SDK polling budget. "
                "The upload may still be processing on the provider.",
                str(exc),
            ) from exc
        except InvalidRequestError as exc:
            raise ProviderRejected(
                f"VideoDB rejected the upload: {_reason(exc)}", str(exc)
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable("VideoDB upload failed.", str(exc)) from exc

        if media is None:
            raise ProviderContractViolation(
                "VideoDB upload returned no media object. AdProof cannot record a "
                "media reference it did not receive."
            )
        if not getattr(media, "id", None):
            raise ProviderContractViolation(
                "VideoDB upload returned a media object without an id."
            )
        if not isinstance(media, videodb.video.Video):
            raise ProviderRejected(
                f"Uploaded source resolved to {type(media).__name__}, not a video. "
                "AdProof verifies video submissions only."
            )

        # The SDK defaults Video.length to 0.0 when the provider omits it, so a
        # zero length is indistinguishable from "not reported". Record it as
        # unknown rather than asserting a duration of zero.
        raw_length = getattr(media, "length", 0.0)
        duration = float(raw_length) if raw_length and float(raw_length) > 0 else None

        return IngestedMedia(
            provider_video_id=media.id,
            provider_collection_id=media.collection_id,
            duration_seconds=duration,
            stream_url=getattr(media, "stream_url", None),
            player_url=getattr(media, "player_url", None),
            snapshot={
                "id": media.id,
                "collection_id": media.collection_id,
                "name": getattr(media, "name", None),
                "length_raw": raw_length,
                "stream_url": getattr(media, "stream_url", None),
                "player_url": getattr(media, "player_url", None),
                "thumbnail_url": getattr(media, "thumbnail_url", None),
                "sdk_version": SDK_VERSION,
            },
        )

    def _get_video(self, provider_video_id: str, collection_id: str | None):
        collection = self.resolve_collection(collection_id)
        try:
            return collection.get_video(provider_video_id)
        except InvalidRequestError as exc:
            raise ProviderRejected(
                f"VideoDB has no video {provider_video_id!r}.", str(exc)
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable(
                "Could not load the video from VideoDB.", str(exc)
            ) from exc

    def generate_stream_url(
        self, provider_video_id: str, collection_id: str | None = None
    ) -> str:
        """Full-video HLS stream, used for seek-to-timestamp playback."""
        video = self._get_video(provider_video_id, collection_id)
        try:
            url = video.generate_stream()
        except RequestTimeoutError as exc:
            raise ProviderTimeout(
                "VideoDB stream generation exceeded the SDK polling budget.", str(exc)
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable(
                "VideoDB could not produce a playable stream.", str(exc)
            ) from exc
        if not url:
            raise ProviderContractViolation(
                "VideoDB returned an empty stream URL; no playback reference can "
                "be recorded."
            )
        return url

    # -- indexing ---------------------------------------------------------

    def index_spoken_words(
        self,
        provider_video_id: str,
        *,
        language_code: str | None = None,
        collection_id: str | None = None,
    ) -> CreatedIndex:
        """Create the spoken-word index.

        Blocking. videodb 0.5.1 index_spoken_words() returns None and exposes
        no index id, so provider_index_id is recorded as null.
        """
        video = self._get_video(provider_video_id, collection_id)
        try:
            video.index_spoken_words(language_code=language_code)
        except RequestTimeoutError as exc:
            raise ProviderTimeout(
                "Spoken-word indexing exceeded the SDK polling budget "
                "(max_poll_time). The index may still be building on the "
                "provider; retry to re-check.",
                str(exc),
            ) from exc
        except InvalidRequestError as exc:
            message = str(exc).lower()
            if any(marker in message for marker in _ALREADY_INDEXED_MARKERS):
                return CreatedIndex(provider_index_id=None, already_existed=True)
            raise ProviderRejected(
                f"VideoDB rejected the spoken-word index request: {_reason(exc)}",
                str(exc),
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable(
                "Spoken-word indexing failed.", str(exc)
            ) from exc
        return CreatedIndex(provider_index_id=None, already_existed=False)

    def index_scenes(
        self,
        provider_video_id: str,
        *,
        prompt: str,
        index_name: str,
        seconds_per_scene: int,
        collection_id: str | None = None,
    ) -> CreatedIndex:
        """Create one focused visual index.

        Uses time-based extraction so the sampling granularity is known and can
        be reported as the measurement resolution. Shot-based extraction would
        give an unknown, content-dependent granularity, which cannot be honestly
        reported as an accuracy bound.
        """
        # VERIFIED 2026-07-26 against the live API: a non-integer `time` is
        # rejected with "'time' in the extraction_config must be a positive
        # integer". Validated here so the failure is a clear local error rather
        # than a wasted provider round-trip.
        if int(seconds_per_scene) != seconds_per_scene or seconds_per_scene < 1:
            raise ProviderRejected(
                f"seconds_per_scene must be a positive integer; got "
                f"{seconds_per_scene!r}. VideoDB rejects non-integer scene "
                f"sampling intervals."
            )

        video = self._get_video(provider_video_id, collection_id)
        extraction_config = {
            "time": int(seconds_per_scene),
            "frame_count": 1,
            "select_frames": ["first"],
        }
        try:
            scene_index_id = video.index_scenes(
                extraction_type=SceneExtractionType.time_based,
                extraction_config=extraction_config,
                prompt=prompt,
                name=index_name,
            )
        except RequestTimeoutError as exc:
            raise ProviderTimeout(
                "Visual indexing exceeded the SDK polling budget (max_poll_time). "
                "The index may still be building on the provider; retry to "
                "re-check.",
                str(exc),
            ) from exc
        except InvalidRequestError as exc:
            # index_scenes() has no `force` parameter and errors when an index
            # already exists. The vendor-documented v1 recovery is to read the
            # existing id back out of the error message. AdProof normally avoids
            # this via its own MediaIndex record, but that record can be absent
            # while the provider index exists (crash between the provider call
            # and the commit), and without this a retry would fail forever.
            #
            # This recovers a REAL provider id from the provider's own message;
            # it does not invent one. If no id is present, the error is raised.
            existing_id = _existing_scene_index_id(exc)
            if existing_id:
                logger.info(
                    "visual index %r already exists on %s as %s; reusing it",
                    index_name,
                    provider_video_id,
                    existing_id,
                )
                return CreatedIndex(
                    provider_index_id=existing_id, already_existed=True
                )
            raise ProviderRejected(
                f"VideoDB rejected the visual index request: {_reason(exc)}",
                str(exc),
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable("Visual indexing failed.", str(exc)) from exc

        if not scene_index_id:
            raise ProviderContractViolation(
                "VideoDB returned no scene_index_id. AdProof will not record a "
                "visual index it cannot address for retrieval."
            )
        return CreatedIndex(provider_index_id=scene_index_id, already_existed=False)

    def wait_for_scene_index(
        self,
        provider_video_id: str,
        scene_index_id: str,
        *,
        collection_id: str | None = None,
        timeout_seconds: float = 600.0,
        poll_seconds: float = 5.0,
    ) -> int:
        """Block until a visual index actually contains records.

        VERIFIED 2026-07-26: index_scenes() returns a scene_index_id long
        before the index is queryable -- it returned in 3.1s for a 9-minute
        video, and searches against it failed with "No results found" for
        minutes afterwards, then succeeded with 56 records present.

        Without this wait, retrieval would run against an empty index and the
        empty result would be classified as absence -- reporting "nothing was
        visible" when the truth is "nothing had been indexed yet". That is
        exactly the absence-vs-incompleteness confusion the product forbids.

        Raises ProviderTimeout (retryable) rather than proceeding with an index
        that is not ready.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self.scene_index_status(
                provider_video_id, scene_index_id, collection_id
            )
            if status is not None and _looks_like_failure(status):
                raise ProviderRejected(
                    f"VideoDB reported status {status!r} for visual index "
                    f"{scene_index_id!r}. The index did not build, so no visual "
                    f"evidence can be retrieved for it.",
                    f"scene_index_id={scene_index_id} status={status}",
                )

            count = self.scene_index_record_count(
                provider_video_id, scene_index_id, collection_id
            )
            # Require BOTH the provider's own status and the presence of
            # records. Status alone was observed to read 'done' on an index
            # that searches could not yet reach, and records alone would not
            # notice a provider-side failure.
            if status == _SCENE_INDEX_READY and count > 0:
                return count
            if time.monotonic() >= deadline:
                raise ProviderTimeout(
                    f"Visual index {scene_index_id!r} was not ready after "
                    f"{timeout_seconds:.0f}s (last status={status!r}, "
                    f"records={count}). It may still be building on "
                    f"the provider; retry to re-check. AdProof will not search "
                    f"an index it cannot confirm is populated.",
                    f"scene_index_id={scene_index_id} video={provider_video_id}",
                )
            logger.info(
                "waiting for visual index %s (status=%r records=%d, %.0fs left)",
                scene_index_id,
                status,
                count,
                deadline - time.monotonic(),
            )
            time.sleep(poll_seconds)

    def scene_index_status(
        self,
        provider_video_id: str,
        scene_index_id: str,
        collection_id: str | None = None,
    ) -> str | None:
        """The provider's own status string for one visual index.

        VERIFIED 2026-07-26: list_scene_index() returns dicts shaped
        {'metadata', 'name', 'scene_index_id', 'status'}, with 'done' observed
        for a completed index. Only 'done' has been observed, so no other value
        is treated as meaningful-and-ready; unknown values simply mean
        not-yet-ready and are logged.

        Returns None when the index is not listed at all.
        """
        video = self._get_video(provider_video_id, collection_id)
        try:
            entries = video.list_scene_index()
        except VideodbError as exc:
            raise ProviderUnavailable(
                "Could not list visual indexes.", str(exc)
            ) from exc
        for entry in entries or []:
            if entry.get("scene_index_id") == scene_index_id:
                return entry.get("status")
        return None

    def scene_index_record_count(
        self,
        provider_video_id: str,
        scene_index_id: str,
        collection_id: str | None = None,
    ) -> int:
        """How many scene records a visual index actually contains.

        Distinguishes "the index is populated and nothing matched" from "the
        index is empty, so the search proved nothing". Without this, an empty
        visual search would be misread as evidence about the media.
        """
        video = self._get_video(provider_video_id, collection_id)
        try:
            records = video.get_scene_index(scene_index_id)
        except InvalidRequestError as exc:
            raise ProviderRejected(
                f"VideoDB could not return scene index {scene_index_id!r}: "
                f"{_reason(exc)}",
                str(exc),
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable(
                "Could not inspect the visual index.", str(exc)
            ) from exc
        return len(records or [])

    def generate_text_json(
        self, prompt: str, *, model_name: str = "pro",
        collection_id: str | None = None,
    ):
        """Text generation via VideoDB (Collection.generate_text, verified in
        0.5.1). Used ONLY to qualify already-retrieved evidence descriptions;
        never to measure, count, or compare against thresholds.
        """
        collection = self.resolve_collection(collection_id)
        try:
            result = collection.generate_text(
                prompt=prompt, model_name=model_name, response_type="json",
                wait=True,
            )
        except RequestTimeoutError as exc:
            raise ProviderTimeout(
                "Evidence qualification exceeded the SDK polling budget.",
                str(exc),
            ) from exc
        except InvalidRequestError as exc:
            raise ProviderRejected(
                f"VideoDB rejected the qualification request: {_reason(exc)}",
                str(exc),
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable(
                "Evidence qualification failed.", str(exc)
            ) from exc
        if isinstance(result, str):
            import json as _json

            try:
                return _json.loads(result)
            except ValueError as exc:
                raise ProviderContractViolation(
                    "Qualification response was not valid JSON."
                ) from exc
        return result

    # -- retrieval --------------------------------------------------------

    def search(
        self,
        provider_video_id: str,
        *,
        query: str,
        index_type: str,
        search_type: str,
        score_threshold: float | None = None,
        result_threshold: int | None = None,
        scene_index_id: str | None = None,
        collection_id: str | None = None,
    ) -> list[RetrievedShot]:
        """Search one video's index.

        Uses legacy_search, which is the SDK surface that lets AdProof name the
        exact index and search mode per rule. That reproducibility is required
        by VIDEODB_INTEGRATION.md s8. The Search V2 helpers (search/ask) are
        deliberately not used: `ask` in particular is a chat-over-video
        affordance that falls outside this product's boundary.

        Returns [] for a genuine empty result. The caller must classify that
        absence; an empty list is never itself a verdict.
        """
        if index_type not in (IndexType.spoken_word, IndexType.scene):
            raise ProviderRejected(f"Unsupported index_type {index_type!r}.")
        if search_type not in (SearchType.semantic, SearchType.keyword):
            raise ProviderRejected(f"Unsupported search_type {search_type!r}.")

        video = self._get_video(provider_video_id, collection_id)
        extra: dict[str, Any] = {}
        if scene_index_id:
            extra["scene_index_id"] = scene_index_id

        try:
            result = video.legacy_search(
                query=query,
                search_type=search_type,
                index_type=index_type,
                score_threshold=score_threshold,
                result_threshold=result_threshold,
                **extra,
            )
        except SearchError as exc:
            raise ProviderRejected(
                f"VideoDB rejected the search: {_reason(exc)}", str(exc)
            ) from exc
        except RequestTimeoutError as exc:
            raise ProviderTimeout(
                "Search exceeded the SDK polling budget.", str(exc)
            ) from exc
        except InvalidRequestError as exc:
            message = str(exc).lower()
            if any(marker in message for marker in _EMPTY_RESULT_MARKERS):
                # A genuine empty result, which the provider reports as an
                # error. Return [] so the caller classifies the absence under
                # the rule's policy instead of recording a provider failure.
                logger.info(
                    "VideoDB reported no results for %r on %s (%s/%s)",
                    query,
                    provider_video_id,
                    search_type,
                    index_type,
                )
                return []
            raise ProviderRejected(
                f"VideoDB rejected the search request: {_reason(exc)}", str(exc)
            ) from exc
        except VideodbError as exc:
            raise ProviderUnavailable("Search failed.", str(exc)) from exc

        shots = getattr(result, "shots", None)
        if shots is None:
            raise ProviderContractViolation(
                "VideoDB search response contained no shots collection."
            )

        normalized: list[RetrievedShot] = []
        for shot in shots:
            start = getattr(shot, "start", None)
            if start is None:
                # A hit without a start time cannot be seeked to and cannot be
                # measured. Dropping it is correct; inventing one is not.
                logger.warning(
                    "Discarding VideoDB shot without a start time (video=%s query=%r)",
                    provider_video_id,
                    query,
                )
                continue
            normalized.append(
                RetrievedShot(
                    start_seconds=float(start),
                    end_seconds=(
                        float(shot.end) if getattr(shot, "end", None) is not None
                        else None
                    ),
                    text=getattr(shot, "text", None),
                    provider_score=(
                        float(shot.search_score)
                        if getattr(shot, "search_score", None) is not None
                        else None
                    ),
                    provider_index_id=getattr(shot, "scene_index_id", None),
                    provider_index_name=getattr(shot, "scene_index_name", None),
                    stream_url=getattr(shot, "stream_url", None),
                    snapshot=_shot_snapshot(shot),
                )
            )
        return normalized
