"""Confidence banding.

QUALITY_AND_EVALUATION.md s6 forbids exposing numeric confidence as
authoritative before calibration, and VERIFICATION_ENGINE.md s7 forbids
collapsing unrelated confidence signals into one number.

So: bands are derived from a single, named signal at a time, the derivation is
recorded, and the band carries an explicit "uncalibrated" marker. Scores from
different index types are never averaged or compared.
"""

from __future__ import annotations

from ..states import CONFIDENCE_MODEL_VERSION, ConfidenceBand

#: Thresholds over the raw VideoDB relevance score. These are provisional and
#: explicitly uncalibrated; QUALITY_AND_EVALUATION.md s6 gates any authoritative
#: numeric display on demonstrated calibration, which has not been done.
_HIGH = 0.75
_MEDIUM = 0.50


def band_for_provider_score(score: float | None) -> ConfidenceBand:
    """Band a single provider relevance score.

    Returns `unavailable` when the provider gave no score, rather than
    defaulting to a band the evidence does not support.
    """
    if score is None:
        return ConfidenceBand.unavailable
    if score >= _HIGH:
        return ConfidenceBand.high
    if score >= _MEDIUM:
        return ConfidenceBand.medium
    return ConfidenceBand.low


def aggregate_band(bands: list[ConfidenceBand]) -> ConfidenceBand:
    """Confidence for a rule result, given its counted evidence.

    Deliberately the WEAKEST-LINK-aware maximum: a result is at most as
    confident as its best single piece of evidence, and `unavailable` when no
    evidence carried a score. This is an ordering over one homogeneous signal
    (provider relevance within a single index), not a fusion of different
    signals.
    """
    ranked = [b for b in bands if b is not ConfidenceBand.unavailable]
    if not ranked:
        return ConfidenceBand.unavailable
    order = {
        ConfidenceBand.low: 0,
        ConfidenceBand.medium: 1,
        ConfidenceBand.high: 2,
    }
    return max(ranked, key=lambda b: order[b])


def derivation_note() -> str:
    """Human-readable statement of how the band was produced."""
    return (
        f"Band derived from the raw VideoDB relevance score by "
        f"{CONFIDENCE_MODEL_VERSION} (high >= {_HIGH}, medium >= {_MEDIUM}). "
        f"This scale is NOT calibrated against observed correctness and must "
        f"not be read as a probability."
    )
