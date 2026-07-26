"""Campaign analytics.

USER_JOURNEYS.md Journey 5 and DELIVERY_ROADMAP.md Phase 8 impose three
constraints that shape everything here:

  * **aggregates link to underlying submissions** — every count carries the
    submission ids behind it, so any figure can be opened and checked;
  * **machine results and final human outcomes stay distinguishable** — they
    are reported as two separate tallies, never merged into one "pass rate";
  * **incomplete work is not silently included** — submissions still processing
    or errored are excluded by default and counted separately, so a small
    denominator is visible rather than flattering.

Pure aggregation over facts already recorded. Nothing is inferred, and a metric
that cannot be computed from real data is reported as unavailable rather than
as zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .states import (
    DecisionType,
    OverrideReason,
    RuleResultState,
    SubmissionState,
)

#: Submissions whose analysis is still in flight or broken. Excluded from
#: outcome rates by default (Phase 8 exit criterion).
INCOMPLETE_STATES = frozenset(
    {
        SubmissionState.draft,
        SubmissionState.ingesting,
        SubmissionState.indexing,
        SubmissionState.evaluating,
        SubmissionState.error,
    }
)


@dataclass(frozen=True)
class RuleOutcome:
    rule_id: str
    requirement_text: str
    machine_state: RuleResultState | None
    human_state: RuleResultState | None
    override_reason: OverrideReason | None = None


@dataclass(frozen=True)
class SubmissionFacts:
    """One submission, flattened for aggregation."""

    submission_id: str
    creator_reference: str
    state: SubmissionState
    rules: list[RuleOutcome]
    final_decision: DecisionType | None = None
    #: Seconds from becoming review-ready to the first decision. None when
    #: either end of that interval is missing.
    time_to_decision_seconds: float | None = None
    has_processing_error: bool = False

    @property
    def is_complete(self) -> bool:
        return self.state not in INCOMPLETE_STATES


@dataclass(frozen=True)
class Tally:
    """A count that can always be opened."""

    count: int
    submission_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuleFailurePattern:
    rule_id: str
    requirement_text: str
    machine_failures: Tally
    human_confirmed_failures: Tally
    overridden_away: Tally
    #: Reason -> count. A cluster here usually means the RULE or the retrieval
    #: is wrong, not the creator.
    override_reasons: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CreatorTrend:
    creator_reference: str
    submissions: Tally
    #: Requirements this creator has failed more than once.
    repeated_failures: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignAnalytics:
    total_submissions: int
    included_submissions: Tally
    excluded_incomplete: Tally
    processing_errors: Tally
    review_ready: Tally
    unresolved: Tally

    #: Deliberately two separate figures, never one blended number.
    machine_pass_rate: float | None
    final_approval_rate: float | None

    failure_patterns: list[RuleFailurePattern]
    creator_trends: list[CreatorTrend]

    override_rate: float | None
    override_reason_totals: dict[str, int]
    median_time_to_decision_seconds: float | None

    #: Metrics that genuinely cannot be produced yet, with the reason. Reported
    #: rather than silently omitted or shown as zero.
    unavailable: dict[str, str] = field(default_factory=dict)


def _rate(numerator: int, denominator: int) -> float | None:
    """A rate over an empty population is unavailable, not zero."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def compute(
    submissions: list[SubmissionFacts], *, include_incomplete: bool = False
) -> CampaignAnalytics:
    """Aggregate one campaign's submissions."""
    incomplete = [s for s in submissions if not s.is_complete]
    included = submissions if include_incomplete else [
        s for s in submissions if s.is_complete
    ]

    errors = [s for s in submissions if s.has_processing_error]
    ready = [s for s in submissions if s.state is SubmissionState.ready_for_review]
    unresolved = [
        s
        for s in submissions
        if s.state
        in (
            SubmissionState.ready_for_review,
            SubmissionState.escalated,
            SubmissionState.changes_requested,
        )
    ]

    # --- machine vs human, kept apart -------------------------------------
    machine_all_pass = [
        s
        for s in included
        if s.rules
        and all(r.machine_state is RuleResultState.passed for r in s.rules)
    ]
    decided = [s for s in included if s.final_decision is not None]
    approved = [s for s in decided if s.final_decision is DecisionType.approve]

    # --- per-rule failure patterns ----------------------------------------
    machine_fail: dict[str, list[str]] = defaultdict(list)
    human_fail: dict[str, list[str]] = defaultdict(list)
    overridden_away: dict[str, list[str]] = defaultdict(list)
    reasons_by_rule: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    labels: dict[str, str] = {}

    reviewed_rules = 0
    overridden_rules = 0
    reason_totals: dict[str, int] = defaultdict(int)

    for submission in included:
        for rule in submission.rules:
            labels[rule.rule_id] = rule.requirement_text
            if rule.machine_state is RuleResultState.failed:
                machine_fail[rule.rule_id].append(submission.submission_id)
            if rule.human_state is not None:
                reviewed_rules += 1
                if rule.human_state is not rule.machine_state:
                    overridden_rules += 1
                    if rule.override_reason:
                        reasons_by_rule[rule.rule_id][
                            rule.override_reason.value
                        ] += 1
                        reason_totals[rule.override_reason.value] += 1
                    if (
                        rule.machine_state is RuleResultState.failed
                        and rule.human_state is RuleResultState.passed
                    ):
                        overridden_away[rule.rule_id].append(
                            submission.submission_id
                        )
            effective = (
                rule.human_state if rule.human_state is not None else rule.machine_state
            )
            if effective is RuleResultState.failed and rule.human_state is not None:
                human_fail[rule.rule_id].append(submission.submission_id)

    patterns = [
        RuleFailurePattern(
            rule_id=rule_id,
            requirement_text=labels[rule_id],
            machine_failures=Tally(
                len(machine_fail.get(rule_id, [])), machine_fail.get(rule_id, [])
            ),
            human_confirmed_failures=Tally(
                len(human_fail.get(rule_id, [])), human_fail.get(rule_id, [])
            ),
            overridden_away=Tally(
                len(overridden_away.get(rule_id, [])),
                overridden_away.get(rule_id, []),
            ),
            override_reasons=dict(reasons_by_rule.get(rule_id, {})),
        )
        for rule_id in labels
        if machine_fail.get(rule_id) or reasons_by_rule.get(rule_id)
    ]
    patterns.sort(key=lambda p: p.machine_failures.count, reverse=True)

    # --- creator trends ----------------------------------------------------
    by_creator: dict[str, list[SubmissionFacts]] = defaultdict(list)
    for submission in included:
        by_creator[submission.creator_reference].append(submission)

    trends: list[CreatorTrend] = []
    for creator, subs in by_creator.items():
        counts: dict[str, int] = defaultdict(int)
        for submission in subs:
            for rule in submission.rules:
                effective = (
                    rule.human_state
                    if rule.human_state is not None
                    else rule.machine_state
                )
                if effective is RuleResultState.failed:
                    counts[rule.requirement_text] += 1
        repeated = {text: n for text, n in counts.items() if n > 1}
        trends.append(
            CreatorTrend(
                creator_reference=creator,
                submissions=Tally(len(subs), [s.submission_id for s in subs]),
                repeated_failures=repeated,
            )
        )
    trends.sort(key=lambda t: (-len(t.repeated_failures), t.creator_reference))

    times = [
        s.time_to_decision_seconds
        for s in included
        if s.time_to_decision_seconds is not None
    ]

    unavailable: dict[str, str] = {}
    if not times:
        unavailable["median_time_to_decision"] = (
            "No submission has both a review-ready timestamp and a decision yet."
        )
    # Resubmission requires more than one submission version, which this build
    # does not support. Reporting 0% would imply a measured result.
    unavailable["resubmission_rate"] = (
        "Resubmission is not implemented: every submission has exactly one "
        "version, so this rate would be structurally zero rather than measured."
    )

    return CampaignAnalytics(
        total_submissions=len(submissions),
        included_submissions=Tally(
            len(included), [s.submission_id for s in included]
        ),
        excluded_incomplete=Tally(
            len(incomplete), [s.submission_id for s in incomplete]
        ),
        processing_errors=Tally(len(errors), [s.submission_id for s in errors]),
        review_ready=Tally(len(ready), [s.submission_id for s in ready]),
        unresolved=Tally(len(unresolved), [s.submission_id for s in unresolved]),
        machine_pass_rate=_rate(len(machine_all_pass), len(included)),
        final_approval_rate=_rate(len(approved), len(decided)),
        failure_patterns=patterns,
        creator_trends=trends,
        override_rate=_rate(overridden_rules, reviewed_rules),
        override_reason_totals=dict(reason_totals),
        median_time_to_decision_seconds=_median(times),
        unavailable=unavailable,
    )
