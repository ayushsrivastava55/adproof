"""Classifying "nothing was found".

The single most consequential rule in this product: a search miss is not proof
of absence (CLAUDE.md, PRODUCT_PRINCIPLES.md s2, VIDEODB_INTEGRATION.md s11).

This module decides WHY nothing was found, and separately, what that permits
the machine to conclude under the rule's configured policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..states import AbsenceClass, AbsencePolicy, RuleResultState


@dataclass(frozen=True)
class Coverage:
    """What processing actually completed for the rule's dependencies."""

    #: Every index this rule needs reached JobState.succeeded.
    indexes_complete: bool
    #: An index this rule needs failed terminally, so it will never complete.
    index_failed: bool
    #: Any retrieval run for this rule failed at the provider.
    retrieval_failed: bool
    #: True when at least one search ran without provider error.
    retrieval_attempted: bool


def classify_absence(coverage: Coverage, *, exact_match_search: bool) -> AbsenceClass:
    """Decide why the retrieval came back empty.

    `exact_match_search` marks a deterministic keyword lookup over a completed
    transcript, where an empty result is much stronger evidence of absence than
    a semantic miss.
    """
    if coverage.index_failed or coverage.retrieval_failed:
        # We never successfully looked. Any statement about the media would be
        # unsupported, so this is a provider failure, not a content finding.
        return AbsenceClass.provider_failure
    if not coverage.indexes_complete or not coverage.retrieval_attempted:
        return AbsenceClass.index_incomplete
    if exact_match_search:
        # Keyword search over a completed transcript: the phrase is not in the
        # transcript. Still not proof it was not said -- transcription may have
        # missed it -- so this is the strongest absence class available, not
        # certainty.
        return AbsenceClass.likely_absent
    # A semantic miss over a completed index. The query may simply not match
    # how the index described the moment.
    return AbsenceClass.low_confidence_absence


def state_for_absence(
    absence: AbsenceClass, policy: AbsencePolicy
) -> RuleResultState:
    """What the machine may conclude from an absence, under the rule's policy.

    A provider failure never produces a verdict about the content, regardless
    of policy: we did not look, so we cannot report on what we did not see.
    """
    if absence is AbsenceClass.provider_failure:
        return RuleResultState.error
    if absence is AbsenceClass.index_incomplete:
        return RuleResultState.processing

    match policy:
        case AbsencePolicy.require_human_review:
            return RuleResultState.human_review_required
        case AbsencePolicy.uncertain:
            return RuleResultState.uncertain
        case AbsencePolicy.fail_when_coverage_complete:
            # Only the strongest absence class may fail, and only because the
            # rule opted in. A low-confidence absence still routes to a human.
            if absence is AbsenceClass.likely_absent:
                return RuleResultState.failed
            return RuleResultState.uncertain

    return RuleResultState.uncertain
