"""Adjudication policy.

The load-bearing tests here are the ones that stop the product recommending a
rejection it cannot justify.
"""

from adproof.policy import RuleView, adjudicate, state_after
from adproof.states import (
    AbsenceClass,
    DecisionType,
    RuleResultState,
    Severity,
    SubmissionState,
)


def rv(
    state,
    *,
    severity=Severity.required,
    absence=AbsenceClass.not_applicable,
    human=None,
    rid="r1",
):
    return RuleView(
        rule_id=rid,
        requirement_text=f"requirement {rid}",
        severity=severity,
        machine_state=state,
        absence_class=absence,
        human_state=human,
    )


# -- gate ------------------------------------------------------------------


def test_nothing_is_permitted_while_processing_is_incomplete():
    gate = adjudicate([rv(RuleResultState.passed)], processing_complete=False)
    assert gate.permitted == frozenset()
    assert gate.recommendation is None
    assert not gate.can_approve


def test_all_passing_permits_approval_and_recommends_it():
    gate = adjudicate(
        [rv(RuleResultState.passed, rid="a"), rv(RuleResultState.passed, rid="b")],
        processing_complete=True,
    )
    assert gate.can_approve
    assert gate.recommendation is DecisionType.approve


def test_unresolved_rule_blocks_approval_but_allows_routing():
    """An uncertain rule nobody has looked at cannot be approved past."""
    gate = adjudicate([rv(RuleResultState.uncertain)], processing_complete=True)
    assert not gate.can_approve
    assert DecisionType.reject not in gate.permitted
    # A reviewer is not stuck: they can still route it.
    assert DecisionType.request_changes in gate.permitted
    assert DecisionType.escalate in gate.permitted
    assert gate.blocking_rule_ids == ["r1"]


def test_human_review_unblocks_approval():
    gate = adjudicate(
        [rv(RuleResultState.uncertain, human=RuleResultState.passed)],
        processing_complete=True,
    )
    assert gate.can_approve


def test_error_and_processing_states_block_approval():
    for state in (RuleResultState.error, RuleResultState.processing):
        gate = adjudicate([rv(state)], processing_complete=True)
        assert not gate.can_approve, state


# -- recommendation --------------------------------------------------------


def test_blocking_failure_on_real_evidence_recommends_rejection():
    gate = adjudicate(
        [rv(RuleResultState.failed, severity=Severity.blocking)],
        processing_complete=True,
    )
    assert gate.recommendation is DecisionType.reject


def test_blocking_failure_resting_only_on_absence_does_not_recommend_rejection():
    """The product's central promise, applied at the decision layer.

    A rule that failed because nothing was found must not drive a rejection
    recommendation until a human has confirmed it.
    """
    gate = adjudicate(
        [
            rv(
                RuleResultState.failed,
                severity=Severity.blocking,
                absence=AbsenceClass.likely_absent,
            )
        ],
        processing_complete=True,
    )
    assert gate.recommendation is DecisionType.request_changes
    assert any("not treated as proof" in r for r in gate.reasons)


def test_absence_based_blocking_failure_recommends_rejection_once_reviewed():
    gate = adjudicate(
        [
            rv(
                RuleResultState.failed,
                severity=Severity.blocking,
                absence=AbsenceClass.likely_absent,
                human=RuleResultState.failed,
            )
        ],
        processing_complete=True,
    )
    assert gate.recommendation is DecisionType.reject


def test_non_blocking_failure_recommends_changes_not_rejection():
    gate = adjudicate(
        [rv(RuleResultState.failed, severity=Severity.required)],
        processing_complete=True,
    )
    assert gate.recommendation is DecisionType.request_changes


def test_optional_failure_does_not_prevent_approval_being_permitted():
    gate = adjudicate(
        [
            rv(RuleResultState.passed, rid="a"),
            rv(
                RuleResultState.failed,
                severity=Severity.optional,
                rid="b",
                human=RuleResultState.failed,
            ),
        ],
        processing_complete=True,
    )
    assert gate.can_approve
    assert gate.recommendation is DecisionType.request_changes
    assert any("optional" in r for r in gate.reasons)


def test_human_override_changes_the_effective_state():
    view = rv(RuleResultState.failed, human=RuleResultState.passed)
    assert view.effective_state is RuleResultState.passed
    assert view.machine_state is RuleResultState.failed, "machine result mutated"


def test_recommendation_is_never_a_decision():
    """The policy recommends; it never records an outcome."""
    gate = adjudicate([rv(RuleResultState.passed)], processing_complete=True)
    assert isinstance(gate.recommendation, DecisionType)
    assert not hasattr(gate, "decided")


def test_state_after_maps_every_decision():
    assert state_after(DecisionType.approve) is SubmissionState.approved
    assert state_after(DecisionType.reject) is SubmissionState.rejected
    assert state_after(DecisionType.request_changes) is (
        SubmissionState.changes_requested
    )
    assert state_after(DecisionType.escalate) is SubmissionState.escalated


def test_auto_mode_bounces_uncertainty_to_creator_never_to_a_verdict():
    """Zero-human mode: no recommendation still terminates as request_changes,
    never as approve or reject -- an unverified requirement is not a verdict."""
    gate = adjudicate([rv(RuleResultState.uncertain)], processing_complete=True)
    assert gate.recommendation is None
    assert DecisionType.approve not in gate.permitted
    assert DecisionType.reject not in gate.permitted
