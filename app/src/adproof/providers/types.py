"""Internal representations of provider output.

The rest of the product depends on these, never on VideoDB response shapes
(SYSTEM_ARCHITECTURE.md, VideoDB adapter).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IngestedMedia:
    provider_video_id: str
    provider_collection_id: str
    #: None when the provider did not report a duration. Not defaulted to 0,
    #: because 0 would be a fabricated measurement.
    duration_seconds: float | None
    stream_url: str | None
    player_url: str | None
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class CreatedIndex:
    #: None for the spoken index: videodb 0.5.1 index_spoken_words() returns
    #: None and exposes no index id. Recorded as null rather than invented.
    provider_index_id: str | None
    #: True when the provider reported the index already existed, so this
    #: attempt created nothing. Kept distinct from a fresh creation.
    already_existed: bool = False


@dataclass(frozen=True)
class RetrievedShot:
    """One provider search hit, normalized.

    `end_seconds` and `provider_score` are Optional because the provider may
    omit them; they are never filled in with a guess.
    """

    start_seconds: float
    end_seconds: float | None
    text: str | None
    provider_score: float | None
    provider_index_id: str | None
    provider_index_name: str | None
    stream_url: str | None
    snapshot: dict[str, Any] = field(default_factory=dict)
