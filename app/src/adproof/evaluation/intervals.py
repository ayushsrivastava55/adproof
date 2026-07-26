"""Interval arithmetic.

Pure functions. No I/O, no provider access, no language model. This module is
the sole authority for durations, counts, and ordering
(PRODUCT_PRINCIPLES.md s3, CLAUDE.md "Deterministic application logic").

Policy decisions encoded here (VERIFICATION_ENGINE.md s6):
  * intervals shorter than `min_segment_seconds` are discarded;
  * intervals within `merge_tolerance_seconds` of each other are merged;
  * merging is idempotent, so provider-side stitching cannot double-count;
  * context padding is never applied before measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Two hits closer than this are treated as one continuous appearance.
DEFAULT_MERGE_TOLERANCE_SECONDS = 0.5
#: Hits shorter than this are treated as noise and discarded.
DEFAULT_MIN_SEGMENT_SECONDS = 0.0


@dataclass(frozen=True, order=True)
class Interval:
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"Interval end {self.end} precedes start {self.start}")

    @property
    def duration(self) -> float:
        return self.end - self.start


def normalize(
    raw: list[tuple[float, float | None]],
    *,
    media_duration_seconds: float | None = None,
    min_segment_seconds: float = DEFAULT_MIN_SEGMENT_SECONDS,
) -> list[Interval]:
    """Turn raw (start, end) pairs into valid intervals.

    Pairs whose end is None are discarded: an appearance with no end has no
    measurable duration, and substituting a default would fabricate one.
    Intervals are clamped to the media duration when it is known.
    """
    out: list[Interval] = []
    for start, end in raw:
        if start is None or end is None:
            continue
        if start < 0 or end < start:
            continue
        if media_duration_seconds is not None:
            start = min(start, media_duration_seconds)
            end = min(end, media_duration_seconds)
        interval = Interval(float(start), float(end))
        if interval.duration < min_segment_seconds:
            continue
        out.append(interval)
    return sorted(out)


def merge(
    intervals: list[Interval],
    *,
    merge_tolerance_seconds: float = DEFAULT_MERGE_TOLERANCE_SECONDS,
) -> list[Interval]:
    """Union overlapping and near-adjacent intervals.

    Idempotent: merge(merge(x)) == merge(x). This matters because VideoDB may
    itself stitch adjacent segments; running our merge over already-stitched
    input produces the same union, so measured duration is unaffected by
    whether the provider stitched.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for current in ordered[1:]:
        last = merged[-1]
        if current.start <= last.end + merge_tolerance_seconds:
            if current.end > last.end:
                merged[-1] = Interval(last.start, current.end)
        else:
            merged.append(current)
    return merged


def total_duration(intervals: list[Interval]) -> float:
    """Sum durations. Caller must pass merged intervals to avoid double count."""
    return sum(interval.duration for interval in intervals)


def intersect_window(
    intervals: list[Interval], window: Interval
) -> list[Interval]:
    """Clip intervals to a required time window."""
    out: list[Interval] = []
    for interval in intervals:
        start = max(interval.start, window.start)
        end = min(interval.end, window.end)
        if end > start:
            out.append(Interval(start, end))
    return out


def count_occurrences(
    intervals: list[Interval],
    *,
    merge_tolerance_seconds: float = DEFAULT_MERGE_TOLERANCE_SECONDS,
) -> int:
    """Count distinct occurrences after deduplication.

    Two hits describing the same moment count once.
    """
    return len(merge(intervals, merge_tolerance_seconds=merge_tolerance_seconds))
