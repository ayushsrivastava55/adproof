"""Rule evaluators, including the failure and absence paths.

The absence tests are the important ones: they encode the product's central
promise that a search miss is not proof of absence.
"""

from adproof.evaluation.absence import Coverage
from adproof.evaluation.evaluators import (
    CountedEvidence,
    evaluate_min_visual_duration,
    evaluate_required_spoken_phrase,
)
from adproof.states import AbsenceClass, AbsencePolicy, ConfidenceBand, RuleResultState

COMPLETE = Coverage(
    indexes_complete=True,
    index_failed=False,
    retrieval_failed=False,
    retrieval_attempted=True,
)
INDEX_PENDING = Coverage(
    indexes_complete=False,
    index_failed=False,
    retrieval_failed=False,
    retrieval_attempted=False,
)
INDEX_FAILED = Coverage(
    indexes_complete=False,
    index_failed=True,
    retrieval_failed=False,
    retrieval_attempted=False,
)
RETRIEVAL_FAILED = Coverage(
    indexes_complete=True,
    index_failed=False,
    retrieval_failed=True,
    retrieval_attempted=False,
)


def ev(eid, start, end=None, score=0.9):
    return CountedEvidence(
        evidence_id=eid, start_seconds=start, end_seconds=end, provider_score=score
    )


# -- required spoken phrase ------------------------------------------------


def test_spoken_phrase_passes_when_threshold_met():
    out = evaluate_required_spoken_phrase(
        phrase="AYUSH20",
        min_occurrences=1,
        evidence=[ev("e1", 12.0, 13.0)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.fail_when_coverage_complete,
    )
    assert out.state is RuleResultState.passed
    assert out.measured_value == 1
    assert out.absence_class is AbsenceClass.not_applicable
    assert out.evidence_ids == ["e1"]


def test_spoken_phrase_deduplicates_repeated_hits_for_same_moment():
    out = evaluate_required_spoken_phrase(
        phrase="AYUSH20",
        min_occurrences=2,
        evidence=[ev("e1", 12.0, 13.0), ev("e2", 12.1, 13.1)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    # Two provider hits describing one utterance is one occurrence, so a
    # two-occurrence requirement is NOT met.
    assert out.measured_value == 1
    assert out.state is RuleResultState.failed


def test_spoken_phrase_counts_hits_without_end_times():
    out = evaluate_required_spoken_phrase(
        phrase="AYUSH20",
        min_occurrences=2,
        evidence=[ev("e1", 5.0, None), ev("e2", 40.0, None)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.measured_value == 2
    assert out.state is RuleResultState.passed


def test_spoken_absence_may_fail_only_under_opted_in_policy():
    out = evaluate_required_spoken_phrase(
        phrase="AYUSH20",
        min_occurrences=1,
        evidence=[],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.fail_when_coverage_complete,
    )
    assert out.state is RuleResultState.failed
    assert out.absence_class is AbsenceClass.likely_absent
    assert "not proof" in out.explanation
    assert out.confidence_band is ConfidenceBand.unavailable


def test_spoken_absence_is_uncertain_under_default_policy():
    out = evaluate_required_spoken_phrase(
        phrase="AYUSH20",
        min_occurrences=1,
        evidence=[],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.uncertain


def test_spoken_absence_routes_to_human_under_review_policy():
    out = evaluate_required_spoken_phrase(
        phrase="AYUSH20",
        min_occurrences=1,
        evidence=[],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.require_human_review,
    )
    assert out.state is RuleResultState.human_review_required


def test_incomplete_index_never_produces_a_verdict():
    out = evaluate_required_spoken_phrase(
        phrase="AYUSH20",
        min_occurrences=1,
        evidence=[],
        coverage=INDEX_PENDING,
        absence_policy=AbsencePolicy.fail_when_coverage_complete,
    )
    assert out.state is RuleResultState.processing
    assert out.absence_class is AbsenceClass.index_incomplete


def test_provider_failure_never_produces_a_content_verdict():
    for coverage in (INDEX_FAILED, RETRIEVAL_FAILED):
        out = evaluate_required_spoken_phrase(
            phrase="AYUSH20",
            min_occurrences=1,
            evidence=[],
            coverage=coverage,
            absence_policy=AbsencePolicy.fail_when_coverage_complete,
        )
        assert out.state is RuleResultState.error
        assert out.absence_class is AbsenceClass.provider_failure


# -- minimum visual duration -----------------------------------------------


def test_visual_duration_passes_clearly_above_threshold():
    out = evaluate_min_visual_duration(
        concept="PulseBar package",
        min_duration_seconds=6.0,
        evidence=[ev("e1", 0, 6), ev("e2", 10, 16)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
        measurement_resolution_seconds=2.0,
    )
    assert out.state is RuleResultState.passed
    assert out.measured_value == 12.0


def test_visual_duration_fails_clearly_below_threshold():
    out = evaluate_min_visual_duration(
        concept="PulseBar package",
        min_duration_seconds=10.0,
        evidence=[ev("e1", 0, 2)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
        measurement_resolution_seconds=2.0,
    )
    assert out.state is RuleResultState.failed
    assert out.measured_value == 2.0


def test_visual_duration_near_threshold_routes_to_human():
    """Sampling granularity, not the content, would decide this. Do not guess."""
    out = evaluate_min_visual_duration(
        concept="PulseBar package",
        min_duration_seconds=6.0,
        evidence=[ev("e1", 0, 5.5)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
        measurement_resolution_seconds=2.0,
    )
    assert out.state is RuleResultState.uncertain
    assert "within one sampling interval" in out.explanation


def test_visual_duration_does_not_double_count_overlap():
    out = evaluate_min_visual_duration(
        concept="package",
        min_duration_seconds=6.0,
        evidence=[ev("e1", 0, 5), ev("e2", 3, 8)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.measured_value == 8.0


def test_visual_evidence_without_end_times_is_unmeasurable_not_zero():
    out = evaluate_min_visual_duration(
        concept="package",
        min_duration_seconds=6.0,
        evidence=[ev("e1", 3.0, None)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.uncertain
    assert out.measured_value is None


def test_visual_absence_never_fails_under_default_policy():
    out = evaluate_min_visual_duration(
        concept="package",
        min_duration_seconds=6.0,
        evidence=[],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.uncertain
    assert out.absence_class is AbsenceClass.low_confidence_absence


def test_visual_absence_cannot_fail_even_under_opt_in_policy():
    """A semantic miss is only low-confidence absence, so it must not fail."""
    out = evaluate_min_visual_duration(
        concept="package",
        min_duration_seconds=6.0,
        evidence=[],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.fail_when_coverage_complete,
    )
    assert out.state is RuleResultState.uncertain


def test_explanation_never_asserts_certainty_language():
    out = evaluate_min_visual_duration(
        concept="package",
        min_duration_seconds=6.0,
        evidence=[],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    banned = ["definitely absent", "guaranteed", "fully compliant", "ai approved"]
    assert not any(phrase in out.explanation.lower() for phrase in banned)


# -- retrieval truncation --------------------------------------------------
#
# Regression: a fixed result_threshold=50 truncated a real measurement from
# 315.5s to 100.1s and reported the undercount as the measured value.


def test_truncated_shortfall_is_uncertain_not_fail():
    """Truncation understates, so a shortfall may be an artefact of the cap."""
    out = evaluate_min_visual_duration(
        concept="package",
        min_duration_seconds=200.0,
        evidence=[ev(f"e{i}", i * 10, i * 10 + 2) for i in range(50)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
        evidence_truncated=True,
    )
    assert out.state is RuleResultState.uncertain
    assert "understates the truth" in out.explanation


def test_truncated_but_threshold_already_met_still_passes():
    """Truncation can only understate, so a met threshold remains valid."""
    out = evaluate_min_visual_duration(
        concept="package",
        min_duration_seconds=6.0,
        evidence=[ev("e1", 0, 20)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
        evidence_truncated=True,
    )
    assert out.state is RuleResultState.passed


def test_truncated_occurrence_shortfall_is_uncertain_not_fail():
    out = evaluate_required_spoken_phrase(
        phrase="AYUSH20",
        min_occurrences=99,
        evidence=[ev(f"e{i}", i * 10, i * 10 + 1) for i in range(50)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.fail_when_coverage_complete,
        evidence_truncated=True,
    )
    assert out.state is RuleResultState.uncertain


def test_untruncated_shortfall_still_fails():
    """The truncation guard must not soften genuine failures."""
    out = evaluate_min_visual_duration(
        concept="package",
        min_duration_seconds=200.0,
        evidence=[ev("e1", 0, 10)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
        evidence_truncated=False,
    )
    assert out.state is RuleResultState.failed


def test_result_threshold_scales_with_media_duration():
    from adproof.retrieval.plan import MIN_RESULT_THRESHOLD, result_threshold_for

    # A 2-hour video at 2s sampling can hold ~3600 segments; the cap must not
    # sit below that or long media is silently truncated.
    assert result_threshold_for(7200, 2) > 3600
    assert result_threshold_for(555, 2) > 275
    # Unknown duration falls back to the floor rather than an invented number.
    assert result_threshold_for(None, 2) == MIN_RESULT_THRESHOLD


# -- forbidden occurrence --------------------------------------------------
#
# The asymmetry: for a FORBIDDEN rule, finding nothing is the good outcome.
# But "we did not find it" is not "it is not there", and this is the rule type
# with the worst false-negative consequences.


def test_forbidden_nothing_found_passes_but_says_it_is_not_proof():
    from adproof.evaluation.evaluators import evaluate_forbidden_occurrence

    out = evaluate_forbidden_occurrence(
        subject="guaranteed weight loss",
        evidence=[],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.passed
    assert "not that it is provably absent" in out.explanation
    assert "false-negative" in out.explanation


def test_forbidden_cannot_pass_when_we_never_looked():
    """A prohibited-content pass requires that the search actually ran."""
    from adproof.evaluation.evaluators import evaluate_forbidden_occurrence

    for coverage in (INDEX_FAILED, RETRIEVAL_FAILED, INDEX_PENDING):
        out = evaluate_forbidden_occurrence(
            subject="banned claim",
            evidence=[],
            coverage=coverage,
            absence_policy=AbsencePolicy.uncertain,
        )
        assert out.state is not RuleResultState.passed, coverage


def test_forbidden_confident_hit_fails():
    from adproof.evaluation.evaluators import evaluate_forbidden_occurrence

    out = evaluate_forbidden_occurrence(
        subject="guaranteed weight loss",
        evidence=[ev("e1", 30.0, 33.0, score=0.95)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.failed
    # Context matters: a phrase can be quoted in order to reject it.
    assert "quoted in order to reject" in out.explanation


def test_forbidden_low_confidence_hit_is_uncertain_not_a_violation():
    from adproof.evaluation.evaluators import evaluate_forbidden_occurrence

    out = evaluate_forbidden_occurrence(
        subject="banned claim",
        evidence=[ev("e1", 30.0, 33.0, score=0.21)],
        coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.uncertain


# -- maximum duration ------------------------------------------------------


def test_max_duration_within_limit_passes():
    from adproof.evaluation.evaluators import evaluate_max_visual_duration

    out = evaluate_max_visual_duration(
        concept="competitor pack", max_duration_seconds=10.0,
        evidence=[ev("e1", 0, 3)], coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.passed


def test_max_duration_exceeded_fails():
    from adproof.evaluation.evaluators import evaluate_max_visual_duration

    out = evaluate_max_visual_duration(
        concept="competitor pack", max_duration_seconds=5.0,
        evidence=[ev("e1", 0, 20)], coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.failed


def test_max_duration_truncated_and_apparently_within_limit_is_uncertain():
    """Truncation understates, so 'within the limit' cannot be trusted."""
    from adproof.evaluation.evaluators import evaluate_max_visual_duration

    out = evaluate_max_visual_duration(
        concept="competitor pack", max_duration_seconds=100.0,
        evidence=[ev("e1", 0, 4)], coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain, evidence_truncated=True,
    )
    assert out.state is RuleResultState.uncertain


# -- required in window ----------------------------------------------------


def test_event_inside_window_passes():
    from adproof.evaluation.evaluators import evaluate_required_in_window

    out = evaluate_required_in_window(
        concept="product appears", window=(0.0, 10.0),
        evidence=[ev("e1", 4.0, 6.0)], coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.passed


def test_event_outside_window_fails_even_though_evidence_exists():
    from adproof.evaluation.evaluators import evaluate_required_in_window

    out = evaluate_required_in_window(
        concept="product appears", window=(0.0, 10.0),
        evidence=[ev("e1", 55.0, 60.0)], coverage=COMPLETE,
        absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.failed
    assert "outside the required window" in out.explanation


# -- sequence --------------------------------------------------------------


def test_sequence_in_order_passes():
    from adproof.evaluation.evaluators import evaluate_sequence

    out = evaluate_sequence(
        first_concept="hook", second_concept="product reveal",
        first_evidence=[ev("a", 2.0, 4.0)], second_evidence=[ev("b", 12.0, 14.0)],
        coverage=COMPLETE, absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.passed
    assert out.measured_value == 10.0


def test_sequence_out_of_order_fails():
    from adproof.evaluation.evaluators import evaluate_sequence

    out = evaluate_sequence(
        first_concept="hook", second_concept="product reveal",
        first_evidence=[ev("a", 30.0, 32.0)], second_evidence=[ev("b", 5.0, 7.0)],
        coverage=COMPLETE, absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.failed


def test_sequence_gap_too_large_fails():
    from adproof.evaluation.evaluators import evaluate_sequence

    out = evaluate_sequence(
        first_concept="hook", second_concept="call to action",
        first_evidence=[ev("a", 1.0, 2.0)], second_evidence=[ev("b", 90.0, 92.0)],
        coverage=COMPLETE, absence_policy=AbsencePolicy.uncertain,
        max_gap_seconds=30.0,
    )
    assert out.state is RuleResultState.failed


def test_sequence_with_a_missing_half_never_asserts_disorder():
    """One event unfound is not proof the order was wrong."""
    from adproof.evaluation.evaluators import evaluate_sequence

    out = evaluate_sequence(
        first_concept="hook", second_concept="reveal",
        first_evidence=[ev("a", 1.0, 2.0)], second_evidence=[],
        coverage=COMPLETE, absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is not RuleResultState.failed
    assert "not a proven absent one" in out.explanation


# -- disclosure ------------------------------------------------------------


def test_disclosure_either_modality_passes_on_one():
    from adproof.evaluation.evaluators import evaluate_disclosure

    out = evaluate_disclosure(
        requirement_text="Advertising disclosure", modality_requirement="either",
        spoken_evidence=[ev("s", 3.0, 5.0)], visual_evidence=[],
        coverage=COMPLETE, absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.passed
    # We check presence, never legal sufficiency.
    assert "not legal sufficiency" in out.explanation


def test_disclosure_both_modalities_fails_on_one():
    from adproof.evaluation.evaluators import evaluate_disclosure

    out = evaluate_disclosure(
        requirement_text="Advertising disclosure", modality_requirement="both",
        spoken_evidence=[ev("s", 3.0, 5.0)], visual_evidence=[],
        coverage=COMPLETE, absence_policy=AbsencePolicy.uncertain,
    )
    assert out.state is RuleResultState.failed
    assert "no visible one" in out.explanation


def test_disclosure_outside_window_is_treated_as_absent():
    from adproof.evaluation.evaluators import evaluate_disclosure

    out = evaluate_disclosure(
        requirement_text="Disclosure in first 5s", modality_requirement="either",
        spoken_evidence=[ev("s", 45.0, 47.0)], visual_evidence=[],
        coverage=COMPLETE, absence_policy=AbsencePolicy.require_human_review,
        window=(0.0, 5.0),
    )
    assert out.state is RuleResultState.human_review_required


# -- subjective ------------------------------------------------------------


def test_subjective_makes_no_machine_claim():
    from adproof.evaluation.evaluators import evaluate_subjective

    out = evaluate_subjective(
        requirement_text="Feels premium", guidance="Check lighting and pacing."
    )
    assert out.state is RuleResultState.human_review_required
    assert out.measured_value is None
    assert "makes no machine claim" in out.explanation
    assert "Check lighting" in out.explanation
