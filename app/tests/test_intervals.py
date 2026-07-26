"""Deterministic interval arithmetic.

These tests exist because durations and counts are the product's load-bearing
claims. If this math is wrong, every downstream verdict is wrong.
"""

import pytest

from adproof.evaluation.intervals import (
    Interval,
    count_occurrences,
    intersect_window,
    merge,
    normalize,
    total_duration,
)


def test_interval_rejects_reversed_bounds():
    with pytest.raises(ValueError):
        Interval(5.0, 2.0)


def test_normalize_discards_hits_without_an_end():
    # A hit with no end has no measurable duration. It must be dropped, not
    # given a default length, which would fabricate visible time.
    assert normalize([(1.0, None)]) == []


def test_normalize_discards_negative_and_reversed():
    assert normalize([(-1.0, 2.0), (5.0, 3.0)]) == []


def test_normalize_clamps_to_media_duration():
    assert normalize([(8.0, 20.0)], media_duration_seconds=10.0) == [
        Interval(8.0, 10.0)
    ]


def test_merge_unions_overlapping():
    assert merge([Interval(0, 5), Interval(3, 8)]) == [Interval(0, 8)]


def test_merge_unions_within_tolerance():
    assert merge([Interval(0, 5), Interval(5.4, 9)]) == [Interval(0, 9)]


def test_merge_keeps_gaps_beyond_tolerance():
    assert merge([Interval(0, 5), Interval(6, 9)]) == [Interval(0, 5), Interval(6, 9)]


def test_merge_swallows_contained_interval():
    assert merge([Interval(0, 10), Interval(2, 4)]) == [Interval(0, 10)]


def test_merge_is_idempotent():
    """Guards against double counting if VideoDB itself stitches segments."""
    raw = [Interval(0, 5), Interval(4, 9), Interval(20, 22)]
    once = merge(raw)
    assert merge(once) == once


def test_total_duration_does_not_double_count_overlap():
    # Naive summation would give 10s; the true visible time is 8s.
    assert total_duration(merge([Interval(0, 5), Interval(3, 8)])) == 8.0


def test_count_occurrences_deduplicates_the_same_moment():
    # The same phrase returned twice by the provider is one occurrence.
    assert count_occurrences([Interval(10, 11), Interval(10.2, 11.1)]) == 1


def test_count_occurrences_counts_distinct_moments():
    assert count_occurrences([Interval(1, 2), Interval(30, 31), Interval(60, 61)]) == 3


def test_intersect_window_clips_to_required_window():
    assert intersect_window([Interval(5, 15)], Interval(0, 10)) == [Interval(5, 10)]


def test_intersect_window_drops_disjoint():
    assert intersect_window([Interval(20, 25)], Interval(0, 10)) == []
