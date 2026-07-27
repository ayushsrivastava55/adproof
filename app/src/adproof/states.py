"""Explicit state vocabularies.

Every state a user can observe is named here. There is deliberately no
"unknown" or "ok" catch-all: a state the system cannot justify must be
representable as such (see AbsenceClass, ConfidenceBand.unavailable).
"""

from enum import StrEnum


class JobType(StrEnum):
    ingest = "ingest"
    index_spoken = "index_spoken"
    index_visual = "index_visual"
    retrieval = "retrieval"
    evaluation = "evaluation"


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    #: Failed, but another bounded attempt is permitted.
    failed_retryable = "failed_retryable"
    #: Failed and will not be retried automatically. Requires human action.
    failed_terminal = "failed_terminal"


#: Job states after which no further work will happen on its own.
TERMINAL_JOB_STATES = frozenset({JobState.succeeded, JobState.failed_terminal})


class SubmissionState(StrEnum):
    """PRD s10 submission states. `archived` is not yet reachable."""

    draft = "draft"
    ingesting = "ingesting"
    indexing = "indexing"
    evaluating = "evaluating"
    ready_for_review = "ready_for_review"
    changes_requested = "changes_requested"
    approved = "approved"
    rejected = "rejected"
    escalated = "escalated"
    error = "error"


class RuleType(StrEnum):
    """The rule categories from PRD s9.

    `placement` is deliberately absent as an automated type: "logo readable",
    "product not obscured" cannot be measured reliably, and PRD s9 forbids
    forcing subjective criteria into objective pass/fail. Author those as
    `subjective_human_review`.
    """

    required_spoken_phrase = "required_spoken_phrase"
    forbidden_spoken_claim = "forbidden_spoken_claim"
    min_visual_duration = "min_visual_duration"
    max_visual_duration = "max_visual_duration"
    required_visual_event = "required_visual_event"
    forbidden_visual_event = "forbidden_visual_event"
    disclosure_present = "disclosure_present"
    sequence = "sequence"
    subjective_human_review = "subjective_human_review"


class VisualIndexDomain(StrEnum):
    """Focused visual indexes (VIDEODB_INTEGRATION.md s7).

    One narrow index per domain rather than a single "describe everything"
    prompt, because narrow prompts retrieve more reliably.
    """

    product_presence = "product_presence"
    disclosure = "disclosure"
    competitor = "competitor"
    product_use = "product_use"
    on_screen_claim = "on_screen_claim"


class ModalityRequirement(StrEnum):
    """How a disclosure may be satisfied (PRD s9, VERIFICATION_ENGINE s5)."""

    spoken_only = "spoken_only"
    visual_only = "visual_only"
    #: Either modality satisfies it.
    either = "either"
    #: Both modalities must be satisfied.
    both = "both"


class Modality(StrEnum):
    spoken = "spoken"
    visual = "visual"


class RuleResultState(StrEnum):
    passed = "pass"
    failed = "fail"
    uncertain = "uncertain"
    not_evaluated = "not_evaluated"
    human_review_required = "human_review_required"
    processing = "processing"
    error = "error"


class AbsenceClass(StrEnum):
    """Why nothing was found. Never collapse these into `fail`.

    Mirrors VIDEODB_INTEGRATION.md s11.
    """

    not_applicable = "not_applicable"  # evidence WAS found; no absence to classify
    likely_absent = "likely_absent"
    low_confidence_absence = "low_confidence_absence"
    index_incomplete = "index_incomplete"
    query_insufficient = "query_insufficient"
    unsupported_modality = "unsupported_modality"
    media_quality_issue = "media_quality_issue"
    provider_failure = "provider_failure"


class AbsencePolicy(StrEnum):
    """Per-rule policy governing what an empty retrieval may conclude."""

    #: Absence can never produce `fail`; it produces `uncertain`.
    uncertain = "uncertain"
    #: Absence always routes to a human.
    require_human_review = "require_human_review"
    #: Absence may produce `fail`, but ONLY when every index the rule depends
    #: on completed successfully (coverage is provably complete).
    fail_when_coverage_complete = "fail_when_coverage_complete"


class ConfidenceBand(StrEnum):
    """Displayed confidence.

    QUALITY_AND_EVALUATION.md s6 forbids presenting numeric confidence as
    authoritative before calibration. Numeric provider scores are stored, but
    the band is what the product asserts, and it is labelled uncalibrated.
    """

    high = "high"
    medium = "medium"
    low = "low"
    unavailable = "unavailable"


class EvidenceOrigin(StrEnum):
    """How an evidence row came to exist.

    `live_provider` is the ONLY member. There is no fixture member, so no code
    path can produce fixture evidence that would render as if it were live.
    A future representative-data mode must add a member here explicitly, which
    forces every consumer's match statement to be updated.
    """

    live_provider = "live_provider"


class EvidenceRole(StrEnum):
    supporting = "supporting"
    conflicting = "conflicting"


#: Version stamps. Persisted on every result so a report identifies exactly
#: what produced it (SYSTEM_ARCHITECTURE.md s5).
RETRIEVAL_PLAN_VERSION = "retrieval-plan/v1"
EVALUATOR_VERSION = "evaluator/v1"
CONFIDENCE_MODEL_VERSION = "provider-score-banding/v1-uncalibrated"
VISUAL_INDEX_PROMPT_VERSION = "visual-index-prompt/v3-action-marker"


class Role(StrEnum):
    """Workspace roles (DATA_MODEL.md, suggested role model).

    Only the roles this build actually enforces are defined. Adding a role
    here without enforcing it would imply a control that does not exist.
    """

    workspace_admin = "workspace_admin"
    campaign_manager = "campaign_manager"
    reviewer = "reviewer"
    analyst = "analyst"


#: Roles permitted to create campaigns and confirm rule sets.
CAN_MANAGE_CAMPAIGNS = frozenset({Role.workspace_admin, Role.campaign_manager})
#: Roles permitted to create submissions and trigger processing.
CAN_SUBMIT = frozenset(
    {Role.workspace_admin, Role.campaign_manager, Role.reviewer}
)
#: Roles permitted to read reports and evidence.
CAN_READ = frozenset(
    {Role.workspace_admin, Role.campaign_manager, Role.reviewer, Role.analyst}
)


class Severity(StrEnum):
    """How much a failed rule matters to the overall recommendation."""

    #: A confirmed failure recommends rejection.
    blocking = "blocking"
    #: A failure recommends changes, but does not by itself reject.
    required = "required"
    #: A failure does not block approval.
    optional = "optional"


class ReviewAction(StrEnum):
    """What a reviewer did to one rule's machine result."""

    #: Agreed with the machine result.
    confirm = "confirm"
    #: Replaced it with a different human conclusion. Requires a reason.
    override = "override"


class OverrideReason(StrEnum):
    """Why a reviewer disagreed.

    Mirrors the feedback taxonomy in QUALITY_AND_EVALUATION.md s10 so override
    reasons feed evaluation directly instead of becoming free text nobody reads.
    """

    false_positive = "false_positive"
    false_negative = "false_negative"
    insufficient_evidence = "insufficient_evidence"
    wrong_timestamp = "wrong_timestamp"
    wrong_rule_interpretation = "wrong_rule_interpretation"
    unsupported_rule = "unsupported_rule"
    policy_disagreement = "policy_disagreement"


class DecisionType(StrEnum):
    """A submission-level operational decision."""

    approve = "approve"
    reject = "reject"
    request_changes = "request_changes"
    escalate = "escalate"


#: Rule-level review: confirm and override.
CAN_REVIEW = frozenset(
    {Role.workspace_admin, Role.campaign_manager, Role.reviewer}
)
#: Final approve/reject. DATA_MODEL.md assigns final decisions to the campaign
#: manager; a reviewer may escalate or request changes but not sign off.
CAN_DECIDE_FINAL = frozenset({Role.workspace_admin, Role.campaign_manager})
#: Non-final routing actions a reviewer may take.
CAN_ROUTE = CAN_REVIEW

#: Machine states a reviewer must resolve before a submission can be approved.
UNRESOLVED_STATES = frozenset(
    {
        RuleResultState.failed,
        RuleResultState.uncertain,
        RuleResultState.human_review_required,
        RuleResultState.processing,
        RuleResultState.error,
        RuleResultState.not_evaluated,
    }
)

POLICY_VERSION = "adjudication-policy/v1"
