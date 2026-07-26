"""Deterministic rule evaluators.

Pure functions over already-retrieved evidence. No I/O, no provider access, no
language model. Explanations are built from the structured result by string
templating, so they cannot assert anything the measurement does not contain
(VERIFICATION_ENGINE.md s10).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..states import (
    EVALUATOR_VERSION,
    AbsenceClass,
    AbsencePolicy,
    ConfidenceBand,
    RuleResultState,
)
from .absence import Coverage, classify_absence, state_for_absence
from .confidence import aggregate_band, band_for_provider_score
from .intervals import (
    Interval,
    count_occurrences,
    intersect_window,
    merge,
    normalize,
    total_duration,
)


@dataclass(frozen=True)
class CountedEvidence:
    """Evidence admitted to the deterministic measurement.

    Only evidence from retrieval runs marked `counts_toward_measurement` may be
    passed here. Semantic "context" hits are deliberately excluded so that
    fuzzy matching cannot manufacture a pass.
    """

    evidence_id: str
    start_seconds: float
    end_seconds: float | None
    provider_score: float | None


@dataclass(frozen=True)
class EvaluationOutcome:
    state: RuleResultState
    absence_class: AbsenceClass
    measured_value: float | None
    measured_unit: str | None
    threshold_value: float | None
    confidence_band: ConfidenceBand
    explanation: str
    measurement_intervals: list[tuple[float, float]] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    evaluator_version: str = EVALUATOR_VERSION


def _bands(evidence: list[CountedEvidence]) -> ConfidenceBand:
    return aggregate_band([band_for_provider_score(e.provider_score) for e in evidence])


def evaluate_required_spoken_phrase(
    *,
    phrase: str,
    min_occurrences: int,
    evidence: list[CountedEvidence],
    coverage: Coverage,
    absence_policy: AbsencePolicy,
    evidence_truncated: bool = False,
) -> EvaluationOutcome:
    """Count distinct exact-phrase occurrences in the spoken index.

    Only exact keyword hits reach this function. Occurrences are deduplicated
    by interval merge, so one phrase returned twice by the provider counts once.
    """
    if not evidence:
        absence = classify_absence(coverage, exact_match_search=True)
        state = state_for_absence(absence, absence_policy)
        return EvaluationOutcome(
            state=state,
            absence_class=absence,
            measured_value=0,
            measured_unit="occurrences",
            threshold_value=float(min_occurrences),
            confidence_band=ConfidenceBand.unavailable,
            explanation=_absence_explanation(
                requirement=f'the phrase "{phrase}" spoken at least '
                f"{min_occurrences} time(s)",
                absence=absence,
                state=state,
            ),
        )

    intervals = normalize(
        [(e.start_seconds, e.end_seconds) for e in evidence]
    )
    # A keyword hit may carry no end time. Such hits still evidence an
    # occurrence, so count them as zero-length points rather than discarding.
    if not intervals:
        intervals = [Interval(e.start_seconds, e.start_seconds) for e in evidence]
    merged = merge(intervals)
    occurrences = count_occurrences(intervals)
    met = occurrences >= min_occurrences

    if not met and evidence_truncated:
        return _truncated_outcome(
            requirement=f'the phrase "{phrase}" spoken at least '
            f"{min_occurrences} time(s)",
            measured_value=float(occurrences),
            measured_unit="occurrences",
            threshold_value=float(min_occurrences),
            confidence_band=_bands(evidence),
            intervals=[(i.start, i.end) for i in merged],
            evidence_ids=[e.evidence_id for e in evidence],
        )

    return EvaluationOutcome(
        state=RuleResultState.passed if met else RuleResultState.failed,
        absence_class=AbsenceClass.not_applicable,
        measured_value=float(occurrences),
        measured_unit="occurrences",
        threshold_value=float(min_occurrences),
        confidence_band=_bands(evidence),
        explanation=(
            f'Required: the phrase "{phrase}" spoken at least '
            f"{min_occurrences} time(s). "
            f"Exact keyword search over the spoken-word index found "
            f"{occurrences} distinct occurrence(s) after deduplication. "
            f"{'Threshold met.' if met else 'Threshold not met.'} "
            f"Open the linked moments to confirm the phrase was spoken as "
            f"transcribed."
        ),
        measurement_intervals=[(i.start, i.end) for i in merged],
        evidence_ids=[e.evidence_id for e in evidence],
    )


def evaluate_min_visual_duration(
    *,
    concept: str,
    min_duration_seconds: float,
    evidence: list[CountedEvidence],
    coverage: Coverage,
    absence_policy: AbsencePolicy,
    measurement_resolution_seconds: float | None = None,
    evidence_truncated: bool = False,
) -> EvaluationOutcome:
    """Sum merged visible duration and compare against the threshold."""
    if not evidence:
        absence = classify_absence(coverage, exact_match_search=False)
        state = state_for_absence(absence, absence_policy)
        return EvaluationOutcome(
            state=state,
            absence_class=absence,
            measured_value=0.0,
            measured_unit="seconds",
            threshold_value=min_duration_seconds,
            confidence_band=ConfidenceBand.unavailable,
            explanation=_absence_explanation(
                requirement=f"{concept} visible for at least "
                f"{min_duration_seconds:g}s",
                absence=absence,
                state=state,
            ),
        )

    intervals = normalize([(e.start_seconds, e.end_seconds) for e in evidence])
    if not intervals:
        # Every hit lacked an end time, so no duration is measurable. Report
        # that honestly instead of measuring zero and failing the rule.
        return EvaluationOutcome(
            state=RuleResultState.uncertain,
            absence_class=AbsenceClass.not_applicable,
            measured_value=None,
            measured_unit="seconds",
            threshold_value=min_duration_seconds,
            confidence_band=_bands(evidence),
            explanation=(
                f"Required: {concept} visible for at least "
                f"{min_duration_seconds:g}s. Evidence was retrieved, but no "
                f"result carried an end timestamp, so visible duration could "
                f"not be measured. Review the linked moments directly."
            ),
            evidence_ids=[e.evidence_id for e in evidence],
        )

    merged = merge(intervals)
    measured = total_duration(merged)
    met = measured >= min_duration_seconds

    resolution_note = ""
    if measurement_resolution_seconds:
        resolution_note = (
            f" Visual sampling granularity is "
            f"{measurement_resolution_seconds:g}s, so this measurement is "
            f"accurate to approximately that resolution."
        )

    # Within one sampling interval of the threshold, the granularity alone can
    # decide the outcome. Route that to a human instead of asserting a verdict
    # the measurement cannot support.
    if (
        measurement_resolution_seconds
        and abs(measured - min_duration_seconds) < measurement_resolution_seconds
    ):
        return EvaluationOutcome(
            state=RuleResultState.uncertain,
            absence_class=AbsenceClass.not_applicable,
            measured_value=measured,
            measured_unit="seconds",
            threshold_value=min_duration_seconds,
            confidence_band=_bands(evidence),
            explanation=(
                f"Required: {concept} visible for at least "
                f"{min_duration_seconds:g}s. Measured {measured:.2f}s across "
                f"{len(merged)} merged interval(s). This is within one sampling "
                f"interval of the threshold, so the measurement cannot decide "
                f"the outcome on its own.{resolution_note} Human review required."
            ),
            measurement_intervals=[(i.start, i.end) for i in merged],
            evidence_ids=[e.evidence_id for e in evidence],
        )

    if not met and evidence_truncated:
        return _truncated_outcome(
            requirement=f"{concept} visible for at least "
            f"{min_duration_seconds:g}s",
            measured_value=measured,
            measured_unit="seconds",
            threshold_value=min_duration_seconds,
            confidence_band=_bands(evidence),
            intervals=[(i.start, i.end) for i in merged],
            evidence_ids=[e.evidence_id for e in evidence],
        )

    return EvaluationOutcome(
        state=RuleResultState.passed if met else RuleResultState.failed,
        absence_class=AbsenceClass.not_applicable,
        measured_value=measured,
        measured_unit="seconds",
        threshold_value=min_duration_seconds,
        confidence_band=_bands(evidence),
        explanation=(
            f"Required: {concept} visible for at least "
            f"{min_duration_seconds:g}s. Measured {measured:.2f}s across "
            f"{len(merged)} merged interval(s) of retrieved visual evidence. "
            f"{'Threshold met.' if met else 'Threshold not met.'}"
            f"{resolution_note} Each interval is playable; confirm the concept "
            f"is genuinely visible before relying on this result."
        ),
        measurement_intervals=[(i.start, i.end) for i in merged],
        evidence_ids=[e.evidence_id for e in evidence],
    )


def _truncated_outcome(
    *,
    requirement: str,
    measured_value: float,
    measured_unit: str,
    threshold_value: float,
    confidence_band: ConfidenceBand,
    intervals: list[tuple[float, float]],
    evidence_ids: list[str],
) -> EvaluationOutcome:
    """Outcome when the evidence set was capped by the retrieval limit.

    Truncation can only UNDERSTATE a count or a duration, never overstate it.
    So a threshold that is already met stays a valid `pass` -- the true value is
    at least as large. A threshold that is NOT met cannot be reported as `fail`,
    because the shortfall may be an artefact of the cap rather than the content.
    That case becomes `uncertain` and says so.
    """
    return EvaluationOutcome(
        state=RuleResultState.uncertain,
        absence_class=AbsenceClass.not_applicable,
        measured_value=measured_value,
        measured_unit=measured_unit,
        threshold_value=threshold_value,
        confidence_band=confidence_band,
        explanation=(
            f"Required: {requirement}. Measured {measured_value:g} "
            f"{measured_unit}, below the threshold of {threshold_value:g} "
            f"{measured_unit} -- but retrieval returned the maximum number of "
            f"results it was allowed, so more evidence may exist and this "
            f"measurement understates the truth. A shortfall that may be an "
            f"artefact of the retrieval limit is not reported as a failure. "
            f"Human review required."
        ),
        measurement_intervals=intervals,
        evidence_ids=evidence_ids,
    )


_ABSENCE_REASONS = {
    AbsenceClass.likely_absent: (
        "Exact keyword search over the completed spoken-word index returned no "
        "match. This is the strongest absence signal available, but it is not "
        "proof: transcription can miss speech."
    ),
    AbsenceClass.low_confidence_absence: (
        "Search over the completed index returned no match. The requirement may "
        "be absent, or the query may not match how the index described it."
    ),
    AbsenceClass.index_incomplete: (
        "The index this rule depends on has not completed, so no conclusion "
        "about the media is possible yet."
    ),
    AbsenceClass.provider_failure: (
        "Retrieval failed at the provider. No conclusion about the media is "
        "possible: the search did not run."
    ),
    AbsenceClass.query_insufficient: (
        "The configured query did not produce usable results."
    ),
    AbsenceClass.unsupported_modality: (
        "This requirement's modality is not supported by the configured indexes."
    ),
    AbsenceClass.media_quality_issue: (
        "Media quality prevented reliable retrieval."
    ),
    AbsenceClass.not_applicable: "",
}


def _absence_explanation(
    *, requirement: str, absence: AbsenceClass, state: RuleResultState
) -> str:
    return (
        f"Required: {requirement}. No matching evidence was found. "
        f"{_ABSENCE_REASONS[absence]} "
        f"Recorded as '{state.value}' under this rule's configured absence "
        f"policy. Absence of evidence is not recorded as evidence of absence."
    )


# --------------------------------------------------------------------------
# additional evaluator families (VERIFICATION_ENGINE.md s5)
# --------------------------------------------------------------------------


def evaluate_forbidden_occurrence(
    *,
    subject: str,
    evidence: list[CountedEvidence],
    coverage: Coverage,
    absence_policy: AbsencePolicy,
    min_confidence: ConfidenceBand = ConfidenceBand.medium,
) -> EvaluationOutcome:
    """Fail when a prohibited thing is found. Used for claims and competitors.

    The asymmetry matters. For a FORBIDDEN rule, finding nothing is the good
    outcome, so an empty result is a `pass` rather than an absence problem.
    But a `pass` here only means "we did not find it", which the explanation
    says plainly: this is the rule type most exposed to false negatives, and
    QUALITY_AND_EVALUATION.md s4 asks us to prioritise a low false-negative
    rate for prohibited claims.
    """
    if not evidence:
        absence = classify_absence(coverage, exact_match_search=False)
        # Coverage problems still block: we cannot say "nothing prohibited was
        # found" if we never actually looked.
        if absence in (AbsenceClass.provider_failure, AbsenceClass.index_incomplete):
            state = state_for_absence(absence, absence_policy)
            return EvaluationOutcome(
                state=state,
                absence_class=absence,
                measured_value=None,
                measured_unit="occurrences",
                threshold_value=0,
                confidence_band=ConfidenceBand.unavailable,
                explanation=_absence_explanation(
                    requirement=f"no occurrence of {subject}",
                    absence=absence,
                    state=state,
                ),
            )
        return EvaluationOutcome(
            state=RuleResultState.passed,
            absence_class=AbsenceClass.not_applicable,
            measured_value=0,
            measured_unit="occurrences",
            threshold_value=0,
            confidence_band=ConfidenceBand.unavailable,
            explanation=(
                f"Prohibited: {subject}. No occurrence was found. This means the "
                f"search did not surface it, not that it is provably absent. "
                f"Prohibited-content rules carry the highest false-negative "
                f"risk; spot-check the media if the campaign is sensitive."
            ),
        )

    # Only sufficiently confident hits may condemn a submission.
    order = {
        ConfidenceBand.unavailable: -1,
        ConfidenceBand.low: 0,
        ConfidenceBand.medium: 1,
        ConfidenceBand.high: 2,
    }
    floor = order[min_confidence]
    qualifying = [
        e for e in evidence
        if order[band_for_provider_score(e.provider_score)] >= floor
    ]

    if not qualifying:
        return EvaluationOutcome(
            state=RuleResultState.uncertain,
            absence_class=AbsenceClass.not_applicable,
            measured_value=float(len(evidence)),
            measured_unit="occurrences",
            threshold_value=0,
            confidence_band=_bands(evidence),
            explanation=(
                f"Prohibited: {subject}. {len(evidence)} possible occurrence(s) "
                f"were retrieved, but none reached the confidence required to "
                f"call this a violation. A person should watch these moments."
            ),
            evidence_ids=[e.evidence_id for e in evidence],
        )

    intervals = normalize([(e.start_seconds, e.end_seconds) for e in qualifying])
    if not intervals:
        intervals = [Interval(e.start_seconds, e.start_seconds) for e in qualifying]
    merged = merge(intervals)
    return EvaluationOutcome(
        state=RuleResultState.failed,
        absence_class=AbsenceClass.not_applicable,
        measured_value=float(len(merged)),
        measured_unit="occurrences",
        threshold_value=0,
        confidence_band=_bands(qualifying),
        explanation=(
            f"Prohibited: {subject}. Found {len(merged)} occurrence(s) at or "
            f"above the required confidence. Open each moment to confirm the "
            f"context before acting: a phrase can be quoted in order to reject "
            f"it, and a competitor can appear incidentally."
        ),
        measurement_intervals=[(i.start, i.end) for i in merged],
        evidence_ids=[e.evidence_id for e in qualifying],
    )


def evaluate_max_visual_duration(
    *,
    concept: str,
    max_duration_seconds: float,
    evidence: list[CountedEvidence],
    coverage: Coverage,
    absence_policy: AbsencePolicy,
    measurement_resolution_seconds: float | None = None,
    evidence_truncated: bool = False,
) -> EvaluationOutcome:
    """Fail when something appears for LONGER than permitted."""
    if not evidence:
        return EvaluationOutcome(
            state=RuleResultState.passed,
            absence_class=AbsenceClass.not_applicable,
            measured_value=0.0,
            measured_unit="seconds",
            threshold_value=max_duration_seconds,
            confidence_band=ConfidenceBand.unavailable,
            explanation=(
                f"Limit: {concept} visible for at most "
                f"{max_duration_seconds:g}s. No appearance was found, so the "
                f"limit is not exceeded. Absence of evidence is not proof of "
                f"absence, but for a maximum the direction is safe."
            ),
        )

    intervals = normalize([(e.start_seconds, e.end_seconds) for e in evidence])
    if not intervals:
        return EvaluationOutcome(
            state=RuleResultState.uncertain,
            absence_class=AbsenceClass.not_applicable,
            measured_value=None,
            measured_unit="seconds",
            threshold_value=max_duration_seconds,
            confidence_band=_bands(evidence),
            explanation=(
                f"Limit: {concept} visible for at most "
                f"{max_duration_seconds:g}s. Evidence was retrieved but carried "
                f"no end timestamps, so duration could not be measured."
            ),
            evidence_ids=[e.evidence_id for e in evidence],
        )

    merged = merge(intervals)
    measured = total_duration(merged)
    # Truncation UNDERSTATES duration. For a maximum, an understated figure that
    # already exceeds the limit is still a real breach, but one that appears
    # within the limit cannot be trusted.
    within = measured <= max_duration_seconds
    if within and evidence_truncated:
        return _truncated_outcome(
            requirement=f"{concept} visible for at most {max_duration_seconds:g}s",
            measured_value=measured,
            measured_unit="seconds",
            threshold_value=max_duration_seconds,
            confidence_band=_bands(evidence),
            intervals=[(i.start, i.end) for i in merged],
            evidence_ids=[e.evidence_id for e in evidence],
        )
    if (
        measurement_resolution_seconds
        and abs(measured - max_duration_seconds) < measurement_resolution_seconds
    ):
        return EvaluationOutcome(
            state=RuleResultState.uncertain,
            absence_class=AbsenceClass.not_applicable,
            measured_value=measured,
            measured_unit="seconds",
            threshold_value=max_duration_seconds,
            confidence_band=_bands(evidence),
            explanation=(
                f"Limit: {concept} at most {max_duration_seconds:g}s. Measured "
                f"{measured:.2f}s, within one sampling interval of the limit, "
                f"so the measurement cannot decide it. Human review required."
            ),
            measurement_intervals=[(i.start, i.end) for i in merged],
            evidence_ids=[e.evidence_id for e in evidence],
        )

    return EvaluationOutcome(
        state=RuleResultState.passed if within else RuleResultState.failed,
        absence_class=AbsenceClass.not_applicable,
        measured_value=measured,
        measured_unit="seconds",
        threshold_value=max_duration_seconds,
        confidence_band=_bands(evidence),
        explanation=(
            f"Limit: {concept} visible for at most {max_duration_seconds:g}s. "
            f"Measured {measured:.2f}s across {len(merged)} merged interval(s). "
            f"{'Within the limit.' if within else 'Limit exceeded.'}"
        ),
        measurement_intervals=[(i.start, i.end) for i in merged],
        evidence_ids=[e.evidence_id for e in evidence],
    )


def evaluate_required_in_window(
    *,
    concept: str,
    window: tuple[float, float],
    evidence: list[CountedEvidence],
    coverage: Coverage,
    absence_policy: AbsencePolicy,
) -> EvaluationOutcome:
    """Require an event inside a specific interval, e.g. the first ten seconds."""
    start, end = window
    label = f"{concept} within {start:g}s to {end:g}s"

    if not evidence:
        absence = classify_absence(coverage, exact_match_search=False)
        state = state_for_absence(absence, absence_policy)
        return EvaluationOutcome(
            state=state,
            absence_class=absence,
            measured_value=0,
            measured_unit="occurrences in window",
            threshold_value=1,
            confidence_band=ConfidenceBand.unavailable,
            explanation=_absence_explanation(
                requirement=label, absence=absence, state=state
            ),
        )

    intervals = normalize([(e.start_seconds, e.end_seconds) for e in evidence])
    if not intervals:
        intervals = [Interval(e.start_seconds, e.start_seconds) for e in evidence]
    inside = intersect_window(merge(intervals), Interval(start, end))
    # A zero-length hit exactly inside the window still counts as an occurrence.
    if not inside:
        inside = [
            Interval(i.start, i.end)
            for i in intervals
            if start <= i.start <= end
        ]

    met = bool(inside)
    return EvaluationOutcome(
        state=RuleResultState.passed if met else RuleResultState.failed,
        absence_class=AbsenceClass.not_applicable,
        measured_value=float(len(inside)),
        measured_unit="occurrences in window",
        threshold_value=1,
        confidence_band=_bands(evidence),
        explanation=(
            f"Required: {label}. "
            + (
                f"Found inside the window at "
                f"{', '.join(f'{i.start:.1f}s' for i in inside[:3])}."
                if met
                else "Evidence was found, but all of it falls outside the "
                "required window."
            )
        ),
        measurement_intervals=[(i.start, i.end) for i in inside],
        evidence_ids=[e.evidence_id for e in evidence],
    )


def evaluate_sequence(
    *,
    first_concept: str,
    second_concept: str,
    first_evidence: list[CountedEvidence],
    second_evidence: list[CountedEvidence],
    coverage: Coverage,
    absence_policy: AbsencePolicy,
    max_gap_seconds: float | None = None,
) -> EvaluationOutcome:
    """Require one event to precede another, optionally within a gap."""
    label = f"{first_concept} before {second_concept}"

    if not first_evidence or not second_evidence:
        absence = classify_absence(coverage, exact_match_search=False)
        state = state_for_absence(absence, absence_policy)
        missing = first_concept if not first_evidence else second_concept
        return EvaluationOutcome(
            state=state,
            absence_class=absence,
            measured_value=None,
            measured_unit="seconds between events",
            threshold_value=max_gap_seconds,
            confidence_band=ConfidenceBand.unavailable,
            explanation=(
                f"Required: {label}. No evidence was found for "
                f"'{missing}', so the ordering cannot be checked. "
                f"An unfound event is not a proven absent one."
            ),
        )

    earliest_first = min(e.start_seconds for e in first_evidence)
    # The relevant second event is the earliest one that occurs after the first.
    later = [e for e in second_evidence if e.start_seconds > earliest_first]
    ordered = bool(later)
    gap = (min(e.start_seconds for e in later) - earliest_first) if later else None

    if not ordered:
        return EvaluationOutcome(
            state=RuleResultState.failed,
            absence_class=AbsenceClass.not_applicable,
            measured_value=None,
            measured_unit="seconds between events",
            threshold_value=max_gap_seconds,
            confidence_band=_bands(first_evidence + second_evidence),
            explanation=(
                f"Required: {label}. '{first_concept}' was found at "
                f"{earliest_first:.1f}s, but no occurrence of "
                f"'{second_concept}' follows it."
            ),
            evidence_ids=[e.evidence_id for e in first_evidence + second_evidence],
        )

    within_gap = max_gap_seconds is None or gap <= max_gap_seconds
    return EvaluationOutcome(
        state=RuleResultState.passed if within_gap else RuleResultState.failed,
        absence_class=AbsenceClass.not_applicable,
        measured_value=round(gap, 2),
        measured_unit="seconds between events",
        threshold_value=max_gap_seconds,
        confidence_band=_bands(first_evidence + second_evidence),
        explanation=(
            f"Required: {label}. '{first_concept}' at {earliest_first:.1f}s, "
            f"then '{second_concept}' {gap:.1f}s later. "
            + (
                "Order satisfied."
                if within_gap
                else f"The gap exceeds the permitted {max_gap_seconds:g}s."
            )
        ),
        evidence_ids=[e.evidence_id for e in first_evidence + second_evidence],
    )


def evaluate_disclosure(
    *,
    requirement_text: str,
    modality_requirement: str,
    spoken_evidence: list[CountedEvidence],
    visual_evidence: list[CountedEvidence],
    coverage: Coverage,
    absence_policy: AbsencePolicy,
    window: tuple[float, float] | None = None,
) -> EvaluationOutcome:
    """Either-modality or both-modalities disclosure, with an optional window.

    Disclosure is the rule type regulators care about most, so a miss here is
    never quietly downgraded: when nothing is found the rule's absence policy
    decides, and the default routes to a human.
    """
    def in_window(items: list[CountedEvidence]) -> list[CountedEvidence]:
        if window is None:
            return items
        start, end = window
        return [e for e in items if start <= e.start_seconds <= end]

    spoken = in_window(spoken_evidence)
    visual = in_window(visual_evidence)
    window_note = (
        f" within {window[0]:g}s to {window[1]:g}s" if window else ""
    )

    match modality_requirement:
        case "spoken_only":
            satisfied, found = bool(spoken), spoken
            how = "spoken"
        case "visual_only":
            satisfied, found = bool(visual), visual
            how = "visible on screen"
        case "both":
            satisfied, found = bool(spoken) and bool(visual), spoken + visual
            how = "both spoken and visible on screen"
        case _:  # either
            satisfied, found = bool(spoken) or bool(visual), spoken + visual
            how = "spoken or visible on screen"

    if not found:
        absence = classify_absence(coverage, exact_match_search=False)
        state = state_for_absence(absence, absence_policy)
        return EvaluationOutcome(
            state=state,
            absence_class=absence,
            measured_value=0,
            measured_unit="disclosures found",
            threshold_value=1,
            confidence_band=ConfidenceBand.unavailable,
            explanation=_absence_explanation(
                requirement=f"{requirement_text} ({how}{window_note})",
                absence=absence,
                state=state,
            ),
        )

    if not satisfied:
        # Something was found, but not in every modality the rule demands.
        present = "spoken" if spoken else "visible"
        missing = "visible" if spoken else "spoken"
        return EvaluationOutcome(
            state=RuleResultState.failed,
            absence_class=AbsenceClass.not_applicable,
            measured_value=1,
            measured_unit="disclosures found",
            threshold_value=2,
            confidence_band=_bands(found),
            explanation=(
                f"Required: {requirement_text}, {how}{window_note}. A {present} "
                f"disclosure was found, but no {missing} one. Both are required "
                f"by this rule."
            ),
            evidence_ids=[e.evidence_id for e in found],
        )

    where = []
    if spoken:
        where.append(f"spoken at {min(e.start_seconds for e in spoken):.1f}s")
    if visual:
        where.append(f"on screen at {min(e.start_seconds for e in visual):.1f}s")
    return EvaluationOutcome(
        state=RuleResultState.passed,
        absence_class=AbsenceClass.not_applicable,
        measured_value=float(len(found)),
        measured_unit="disclosures found",
        threshold_value=1,
        confidence_band=_bands(found),
        explanation=(
            f"Required: {requirement_text}, {how}{window_note}. Found "
            f"{' and '.join(where)}. Confirm the wording is adequate for your "
            f"jurisdiction: AdProof checks presence, not legal sufficiency."
        ),
        evidence_ids=[e.evidence_id for e in found],
    )


def evaluate_subjective(*, requirement_text: str, guidance: str | None) -> EvaluationOutcome:
    """Route to a human. No search is run and no machine claim is made."""
    return EvaluationOutcome(
        state=RuleResultState.human_review_required,
        absence_class=AbsenceClass.not_applicable,
        measured_value=None,
        measured_unit=None,
        threshold_value=None,
        confidence_band=ConfidenceBand.unavailable,
        explanation=(
            f"Requires human judgement: {requirement_text}. AdProof did not "
            f"search for or evaluate this and makes no machine claim about it."
            + (f" Reviewer guidance: {guidance}" if guidance else "")
        ),
    )
