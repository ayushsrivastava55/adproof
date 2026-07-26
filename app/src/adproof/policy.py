"""Adjudication policy.

Pure functions. Decides what a submission's evidence *permits*, never what
happens: this module produces a recommendation and a gate, and a human takes
the action (VERIFICATION_ENGINE.md s9, SYSTEM_ARCHITECTURE.md s3).

Two rules hold throughout:

  * nothing is auto-approved or auto-rejected here; the caller must be a
    person with the right role;
  * a rule whose result depends on ABSENCE can never, on its own, drive a
    rejection recommendation without a human having looked at it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .states import (
    POLICY_VERSION,
    AbsenceClass,
    DecisionType,
    RuleResultState,
    Severity,
)


@dataclass(frozen=True)
class RuleView:
    """One rule's standing: what the machine said, and what a human said."""

    rule_id: str
    requirement_text: str
    severity: Severity
    machine_state: RuleResultState | None
    absence_class: AbsenceClass | None
    #: None when no human has reviewed this rule yet.
    human_state: RuleResultState | None = None

    @property
    def effective_state(self) -> RuleResultState | None:
        """The human conclusion when one exists, else the machine's.

        The machine result is never mutated; this is a read-time overlay, so
        both remain separately visible.
        """
        return self.human_state if self.human_state is not None else self.machine_state

    @property
    def is_reviewed(self) -> bool:
        return self.human_state is not None

    @property
    def is_resolved(self) -> bool:
        """Resolved means: passing, or a human has taken a position on it."""
        state = self.effective_state
        if state is RuleResultState.passed:
            return True
        if state is None:
            return False
        return self.is_reviewed

    @property
    def depends_on_absence(self) -> bool:
        return self.absence_class not in (None, AbsenceClass.not_applicable)


@dataclass(frozen=True)
class Adjudication:
    """What the evidence permits, and why."""

    #: Machine recommendation. Advisory only.
    recommendation: DecisionType | None
    #: Decisions a human may currently take.
    permitted: frozenset[DecisionType]
    #: Rules that must be reviewed before approval is possible.
    blocking_rule_ids: list[str] = field(default_factory=list)
    #: Human-readable explanation, shown to the reviewer.
    reasons: list[str] = field(default_factory=list)
    policy_version: str = POLICY_VERSION

    @property
    def can_approve(self) -> bool:
        return DecisionType.approve in self.permitted


def adjudicate(rules: list[RuleView], *, processing_complete: bool) -> Adjudication:
    """Compute the gate and recommendation for one submission version."""
    reasons: list[str] = []

    if not processing_complete:
        return Adjudication(
            recommendation=None,
            permitted=frozenset(),
            blocking_rule_ids=[r.rule_id for r in rules if not r.is_resolved],
            reasons=[
                "Processing has not finished. No decision is available until "
                "every stage reaches a terminal state."
            ],
        )

    unresolved = [r for r in rules if not r.is_resolved]
    failing = [
        r for r in rules if r.effective_state is RuleResultState.failed
    ]
    blocking_failures = [r for r in failing if r.severity is Severity.blocking]

    # Approval requires that every non-passing rule has been looked at by a
    # human. A machine `pass` is enough on its own; anything else is not.
    permitted: set[DecisionType] = {
        DecisionType.request_changes,
        DecisionType.escalate,
    }
    if not unresolved:
        permitted.add(DecisionType.approve)
        permitted.add(DecisionType.reject)
    else:
        reasons.append(
            f"{len(unresolved)} requirement(s) still need review before this "
            f"submission can be approved or rejected."
        )

    # --- recommendation -------------------------------------------------
    recommendation: DecisionType | None = None

    if blocking_failures:
        # A blocking failure resting only on absence is not grounds for a
        # rejection recommendation unless a human confirmed it.
        substantiated = [
            r for r in blocking_failures
            if not r.depends_on_absence or r.is_reviewed
        ]
        if substantiated:
            recommendation = DecisionType.reject
            reasons.append(
                f"{len(substantiated)} blocking requirement(s) failed: "
                + "; ".join(r.requirement_text for r in substantiated[:3])
            )
        else:
            recommendation = DecisionType.request_changes
            reasons.append(
                "A blocking requirement failed only because no evidence was "
                "found. Absence is not treated as proof, so this is not a "
                "rejection recommendation until a reviewer confirms it."
            )
    elif failing:
        recommendation = DecisionType.request_changes
        reasons.append(
            f"{len(failing)} requirement(s) failed but none are blocking."
        )
    elif unresolved:
        recommendation = None
        reasons.append(
            "Some requirements could not be decided automatically and need "
            "human judgement."
        )
    else:
        recommendation = DecisionType.approve
        reasons.append("Every requirement passed or was resolved by a reviewer.")

    optional_failures = [
        r for r in failing if r.severity is Severity.optional
    ]
    if optional_failures and recommendation is DecisionType.request_changes:
        reasons.append(
            f"{len(optional_failures)} of the failures are optional and do not "
            f"block approval."
        )

    return Adjudication(
        recommendation=recommendation,
        permitted=frozenset(permitted),
        blocking_rule_ids=[r.rule_id for r in unresolved],
        reasons=reasons,
    )


def state_after(decision: DecisionType) -> str:
    """The submission state a decision moves the submission into."""
    from .states import SubmissionState

    return {
        DecisionType.approve: SubmissionState.approved,
        DecisionType.reject: SubmissionState.rejected,
        DecisionType.request_changes: SubmissionState.changes_requested,
        DecisionType.escalate: SubmissionState.escalated,
    }[decision]
