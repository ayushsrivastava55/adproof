"""Persistence model for the Phase 1 slice.

Scope note: this is a strict subset of docs/DATA_MODEL.md. Entities not needed
to prove the slice (Brand, Product, Creator, Review, Decision, EvidenceReel,
Integration) are intentionally absent rather than present-but-empty.

`workspace_id` exists on every row from day one so that Phase 5 can enforce
isolation without a migration, but this slice does NOT enforce authorization.
That gap is stated in the UI, not hidden.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .states import (
    AbsenceClass,
    AbsencePolicy,
    ConfidenceBand,
    DecisionType,
    EvidenceOrigin,
    EvidenceRole,
    JobState,
    JobType,
    Modality,
    ModalityRequirement,
    OverrideReason,
    ReviewAction,
    Role,
    RuleResultState,
    RuleType,
    Severity,
    SubmissionState,
    VisualIndexDomain,
)

JSONType = JSONB().with_variant(JSON(), "sqlite")


class EnumType(TypeDecorator):
    """Store a StrEnum as text, and load it back AS THE ENUM.

    Without this, values round-trip as plain `str`, so `is` comparisons against
    enum members silently evaluate False and `.value` raises. That failure mode
    is dangerous here: a state check that quietly stops matching would skip
    pipeline work while every stage still looked healthy.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class, *args, **kwargs) -> None:
        self.enum_class = enum_class
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return self.enum_class(value).value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return self.enum_class(value)


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    #: VideoDB collection backing this workspace. One collection per workspace
    #: (VIDEODB_INTEGRATION.md s4). Resolved lazily on first ingestion.
    provider_collection_id: Mapped[str | None] = mapped_column(String)


class User(Base, TimestampMixin):
    """An authenticated person.

    `password_hash` is never serialised into any response. There is no
    plaintext password column and no password recovery in this build.
    """

    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Membership(Base, TimestampMixin):
    """Connects a user to a workspace with a role.

    Absence of a row is the authorization boundary: no membership means no
    access to anything in that workspace.
    """

    __tablename__ = "membership"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.id"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace.id"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(EnumType(Role), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_membership"),
    )


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaign"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)


class BriefVersion(Base, TimestampMixin):
    """Immutable snapshot of the original brief text.

    Preserved verbatim as an audit artifact (PRODUCT_PRINCIPLES.md s11) even
    though this slice does not extract rules from it.
    """

    __tablename__ = "brief_version"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaign.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("campaign_id", "version"),)


class RuleSetVersion(Base, TimestampMixin):
    """Immutable once confirmed. Rules only evaluate from a confirmed set."""

    __tablename__ = "rule_set_version"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaign.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String)

    rules: Mapped[list[Rule]] = relationship(back_populates="rule_set")

    __table_args__ = (UniqueConstraint("campaign_id", "version"),)

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class Rule(Base, TimestampMixin):
    __tablename__ = "rule"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rule_set_version_id: Mapped[str] = mapped_column(
        ForeignKey("rule_set_version.id"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_type: Mapped[RuleType] = mapped_column(EnumType(RuleType), nullable=False)
    modality: Mapped[Modality] = mapped_column(EnumType(Modality), nullable=False)
    #: Human-readable normalized requirement shown to the reviewer.
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Verbatim span from the brief this rule came from, when known.
    source_brief_excerpt: Mapped[str | None] = mapped_column(Text)

    # --- spoken parameters ---
    #: Exact phrase for required_spoken_phrase.
    phrase: Mapped[str | None] = mapped_column(String)
    #: Phrases that must NOT be said, for forbidden_spoken_claim. Each is
    #: searched separately so evidence points at the specific offending phrase.
    forbidden_phrases: Mapped[list | None] = mapped_column(JSONType)
    min_occurrences: Mapped[int | None] = mapped_column(Integer)

    # --- visual parameters ---
    visual_concept: Mapped[str | None] = mapped_column(String)
    #: Which focused index this rule retrieves from.
    visual_domain: Mapped[VisualIndexDomain | None] = mapped_column(
        EnumType(VisualIndexDomain)
    )
    min_duration_seconds: Mapped[float | None] = mapped_column(Float)
    #: For max_visual_duration: the ceiling a concept may appear for.
    max_duration_seconds: Mapped[float | None] = mapped_column(Float)
    #: Provider score below which a hit is not counted. Materially changes the
    #: measurement, so it is surfaced in the report.
    score_threshold: Mapped[float | None] = mapped_column(Float)

    # --- time window (required_visual_event, disclosure_present) ---
    window_start_seconds: Mapped[float | None] = mapped_column(Float)
    window_end_seconds: Mapped[float | None] = mapped_column(Float)

    # --- disclosure ---
    modality_requirement: Mapped[ModalityRequirement | None] = mapped_column(
        EnumType(ModalityRequirement)
    )

    # --- sequence ---
    sequence_first: Mapped[str | None] = mapped_column(String)
    sequence_second: Mapped[str | None] = mapped_column(String)
    #: Largest permitted gap between the two events, in seconds. Null means any.
    sequence_max_gap_seconds: Mapped[float | None] = mapped_column(Float)

    #: Free-text instruction shown to the reviewer for subjective rules.
    reviewer_guidance: Mapped[str | None] = mapped_column(Text)

    absence_policy: Mapped[AbsencePolicy] = mapped_column(EnumType(AbsencePolicy), nullable=False)
    #: How much a failure of this rule matters. Drives the overall
    #: recommendation (VERIFICATION_ENGINE.md s9).
    severity: Mapped[Severity] = mapped_column(
        EnumType(Severity), nullable=False, server_default=Severity.required.value
    )
    #: Subjective rules are never machine-evaluated (PRD s9).
    requires_human_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    rule_set: Mapped[RuleSetVersion] = relationship(back_populates="rules")

    # Each type must carry the parameters its evaluator needs. Enforced in the
    # database so a malformed rule cannot reach the evaluator at all.
    __table_args__ = (
        CheckConstraint(
            "(rule_type <> 'required_spoken_phrase') OR "
            "(phrase IS NOT NULL AND min_occurrences IS NOT NULL)",
            name="ck_rule_required_phrase",
        ),
        CheckConstraint(
            "(rule_type <> 'forbidden_spoken_claim') OR forbidden_phrases IS NOT NULL",
            name="ck_rule_forbidden_claim",
        ),
        CheckConstraint(
            "(rule_type <> 'min_visual_duration') OR "
            "(visual_concept IS NOT NULL AND min_duration_seconds IS NOT NULL)",
            name="ck_rule_min_duration",
        ),
        CheckConstraint(
            "(rule_type <> 'max_visual_duration') OR "
            "(visual_concept IS NOT NULL AND max_duration_seconds IS NOT NULL)",
            name="ck_rule_max_duration",
        ),
        CheckConstraint(
            "(rule_type NOT IN ('required_visual_event','forbidden_visual_event')) "
            "OR visual_concept IS NOT NULL",
            name="ck_rule_visual_event",
        ),
        CheckConstraint(
            "(rule_type <> 'disclosure_present') OR modality_requirement IS NOT NULL",
            name="ck_rule_disclosure",
        ),
        CheckConstraint(
            "(rule_type <> 'sequence') OR "
            "(sequence_first IS NOT NULL AND sequence_second IS NOT NULL)",
            name="ck_rule_sequence",
        ),
        CheckConstraint(
            "window_start_seconds IS NULL OR window_end_seconds IS NULL "
            "OR window_end_seconds > window_start_seconds",
            name="ck_rule_window_ordered",
        ),
    )


class Submission(Base, TimestampMixin):
    __tablename__ = "submission"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace.id"), nullable=False, index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaign.id"), nullable=False, index=True
    )
    creator_reference: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[SubmissionState] = mapped_column(EnumType(SubmissionState), nullable=False)
    #: Client-supplied key making submission creation idempotent
    #: (SYSTEM_ARCHITECTURE.md s6).
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    #: Populated only when state == error. Never used to soften a failure.
    error_summary: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_submission_idem"),
    )


class SubmissionVersion(Base, TimestampMixin):
    """Immutable media version. Enforced by DB trigger, not convention."""

    __tablename__ = "submission_version"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submission.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Rule set this version is evaluated against, pinned at creation.
    rule_set_version_id: Mapped[str] = mapped_column(
        ForeignKey("rule_set_version.id"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String, nullable=False)  # url | upload
    source_url: Mapped[str | None] = mapped_column(Text)
    source_file_path: Mapped[str | None] = mapped_column(Text)
    submitted_by: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (UniqueConstraint("submission_id", "version"),)


class MediaAsset(Base, TimestampMixin):
    """Internal record mapping to VideoDB provider references.

    Provider IDs are recorded for provenance and are never used as an
    authorization boundary (DATA_MODEL.md, provider references).
    """

    __tablename__ = "media_asset"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    submission_version_id: Mapped[str] = mapped_column(
        ForeignKey("submission_version.id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False, default="videodb")
    provider_video_id: Mapped[str | None] = mapped_column(String)
    provider_collection_id: Mapped[str | None] = mapped_column(String)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    provider_stream_url: Mapped[str | None] = mapped_column(Text)
    provider_player_url: Mapped[str | None] = mapped_column(Text)
    #: Verbatim provider payload retained so any evidence row can be traced
    #: back to what the provider actually returned.
    provider_snapshot: Mapped[dict | None] = mapped_column(JSONType)
    sdk_version: Mapped[str | None] = mapped_column(String)


class MediaIndex(Base, TimestampMixin):
    """One spoken or visual index over a media asset.

    Existence of a row is NOT evidence of completion; `job.state` is the
    authority. `provider_index_id` is null for the spoken index because
    videodb 0.5.1's index_spoken_words() returns None.
    """

    __tablename__ = "media_index"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    media_asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_asset.id"), nullable=False, index=True
    )
    modality: Mapped[Modality] = mapped_column(EnumType(Modality), nullable=False)
    #: Our logical name, also passed to VideoDB for scene indexes.
    index_name: Mapped[str] = mapped_column(String, nullable=False)
    provider_index_id: Mapped[str | None] = mapped_column(String)
    #: Exact prompt used for a focused visual index; null for spoken.
    prompt: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(String)
    extraction_config: Mapped[dict | None] = mapped_column(JSONType)
    #: Sampling granularity in seconds. Bounds achievable duration accuracy and
    #: is surfaced to the reviewer rather than hidden.
    measurement_resolution_seconds: Mapped[float | None] = mapped_column(Float)
    #: Scene records confirmed present before this index was used for
    #: retrieval. Proves the index was populated, so an empty search result can
    #: be attributed to the content rather than to an unbuilt index.
    record_count: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("media_asset_id", "index_name", name="uq_index_per_asset"),
    )


class ProcessingJob(Base, TimestampMixin):
    """Durable job record. Also the audit record for one provider operation.

    Because videodb 0.5.1 performs its own blocking internal polling and
    exposes no job-status endpoint for indexing, THIS ROW is the authority on
    async state. The system does not claim to poll VideoDB for index status.
    """

    __tablename__ = "processing_job"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    submission_version_id: Mapped[str] = mapped_column(
        ForeignKey("submission_version.id"), nullable=False, index=True
    )
    job_type: Mapped[JobType] = mapped_column(EnumType(JobType), nullable=False)
    state: Mapped[JobState] = mapped_column(EnumType(JobState), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    #: Deduplication key preventing duplicate provider work.
    dedupe_key: Mapped[str] = mapped_column(String, nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String)
    #: Short, user-facing. Must describe the real failure, never soften it.
    error_summary: Mapped[str | None] = mapped_column(Text)
    #: Full diagnostic detail for authorized operators.
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_job_dedupe"),
        Index("ix_job_state_type", "state", "job_type"),
    )

    @property
    def elapsed_seconds(self) -> float | None:
        if not self.started_at:
            return None
        end = self.finished_at or utcnow()
        return (end - self.started_at).total_seconds()


class RetrievalRun(Base, TimestampMixin):
    """Exact search plan and provider interaction used to retrieve evidence.

    Immutable once finished. Recorded even when it returns nothing, because an
    empty run is itself a finding that must be auditable.
    """

    __tablename__ = "retrieval_run"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    submission_version_id: Mapped[str] = mapped_column(
        ForeignKey("submission_version.id"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(ForeignKey("rule.id"), nullable=False)
    plan_version: Mapped[str] = mapped_column(String, nullable=False)
    #: Verbatim parameters sent to the provider.
    query: Mapped[str] = mapped_column(Text, nullable=False)
    search_type: Mapped[str] = mapped_column(String, nullable=False)
    index_type: Mapped[str] = mapped_column(String, nullable=False)
    provider_index_id: Mapped[str | None] = mapped_column(String)
    request_params: Mapped[dict] = mapped_column(JSONType, nullable=False)
    #: Whether results from this run count toward the deterministic measurement,
    #: or are retrieved only as reviewer context. Keeps semantic drift out of
    #: exact-phrase counting.
    counts_toward_measurement: Mapped[bool] = mapped_column(Boolean, nullable=False)
    role: Mapped[EvidenceRole] = mapped_column(EnumType(EvidenceRole), nullable=False)
    result_count: Mapped[int | None] = mapped_column(Integer)
    #: True when the provider returned exactly as many results as we asked for,
    #: meaning more may exist and this run UNDERSTATES the truth. A measurement
    #: derived from a truncated run may not be presented as definitive.
    result_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: The cap actually requested, recorded for reproducibility.
    result_threshold: Mapped[int | None] = mapped_column(Integer)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)


class EvidenceItem(Base, TimestampMixin):
    """Timestamped media evidence with full provenance (PRD s11).

    Every field required by PRD s11 is non-optional except those the provider
    genuinely may not supply (score, end), which are nullable so that "absent"
    is distinguishable from "zero".
    """

    __tablename__ = "evidence_item"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    retrieval_run_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_run.id"), nullable=False, index=True
    )
    media_asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_asset.id"), nullable=False
    )
    origin: Mapped[EvidenceOrigin] = mapped_column(EnumType(EvidenceOrigin), nullable=False)
    role: Mapped[EvidenceRole] = mapped_column(EnumType(EvidenceRole), nullable=False)
    modality: Mapped[Modality] = mapped_column(EnumType(Modality), nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float | None] = mapped_column(Float)
    text: Mapped[str | None] = mapped_column(Text)
    #: Raw, uncalibrated provider score. Never presented as authoritative.
    provider_score: Mapped[float | None] = mapped_column(Float)
    confidence_band: Mapped[ConfidenceBand] = mapped_column(EnumType(ConfidenceBand), nullable=False)
    provider_index_id: Mapped[str | None] = mapped_column(String)
    provider_index_name: Mapped[str | None] = mapped_column(String)
    provider_stream_url: Mapped[str | None] = mapped_column(Text)
    #: Evidence-qualification verdict: supports / contradicts / unsure, or
    #: null where qualification does not apply (exact keyword hits, disclosure
    #: marker evidence). Set at insert time; rows are immutable.
    qualification: Mapped[str | None] = mapped_column(String)
    qualifier_version: Mapped[str | None] = mapped_column(String)
    #: Verbatim provider result, retained for dispute resolution.
    provider_snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)

    __table_args__ = (
        CheckConstraint("start_seconds >= 0", name="ck_evidence_start_nonneg"),
        CheckConstraint(
            "end_seconds IS NULL OR end_seconds >= start_seconds",
            name="ck_evidence_interval_ordered",
        ),
    )


class EvaluationResult(Base, TimestampMixin):
    """Machine result for one rule. Immutable once written.

    Held separate from any future human decision so the two never merge
    (PRODUCT_PRINCIPLES.md s7).
    """

    __tablename__ = "evaluation_result"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    submission_version_id: Mapped[str] = mapped_column(
        ForeignKey("submission_version.id"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(ForeignKey("rule.id"), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[RuleResultState] = mapped_column(EnumType(RuleResultState), nullable=False)
    absence_class: Mapped[AbsenceClass] = mapped_column(EnumType(AbsenceClass), nullable=False)
    #: Deterministic measurement: measured value and the threshold applied.
    measured_value: Mapped[float | None] = mapped_column(Float)
    measured_unit: Mapped[str | None] = mapped_column(String)
    threshold_value: Mapped[float | None] = mapped_column(Float)
    #: Accuracy bound implied by index sampling granularity.
    measurement_resolution_seconds: Mapped[float | None] = mapped_column(Float)
    confidence_band: Mapped[ConfidenceBand] = mapped_column(EnumType(ConfidenceBand), nullable=False)
    #: Grounded, template-generated. No LLM participates in this slice.
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    #: Merged intervals actually used for the measurement, for reviewer audit.
    measurement_intervals: Mapped[list | None] = mapped_column(JSONType)
    evidence_ids: Mapped[list | None] = mapped_column(JSONType)

    __table_args__ = (
        UniqueConstraint(
            "submission_version_id", "rule_id", name="uq_eval_per_rule_version"
        ),
    )


class AuditEvent(Base, TimestampMixin):
    """Append-only record of material actions (SECURITY_AND_PRIVACY.md s7)."""

    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    subject_id: Mapped[str] = mapped_column(String, nullable=False)
    #: "system" in this slice; becomes a real principal in Phase 5.
    actor: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONType)


class RuleReview(Base, TimestampMixin):
    """One human action on one rule result. Append-only.

    The machine result is NOT modified. This row records what a human concluded
    alongside it, together with a snapshot of the machine state at the moment of
    review, so the record survives even if evaluation is later re-run
    (VERIFICATION_ENGINE.md s11, PRODUCT_PRINCIPLES.md s9).
    """

    __tablename__ = "rule_review"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    submission_version_id: Mapped[str] = mapped_column(
        ForeignKey("submission_version.id"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(ForeignKey("rule.id"), nullable=False)
    #: Null until the report exists; identifies exactly which machine result
    #: was under review.
    evaluation_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_result.id")
    )
    reviewer_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.id"), nullable=False
    )
    reviewer_email: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[ReviewAction] = mapped_column(
        EnumType(ReviewAction), nullable=False
    )
    #: Immutable copy of what the machine said, so an override remains
    #: interpretable forever.
    machine_state: Mapped[RuleResultState] = mapped_column(
        EnumType(RuleResultState), nullable=False
    )
    #: The human conclusion. Equals machine_state for a confirmation.
    human_state: Mapped[RuleResultState] = mapped_column(
        EnumType(RuleResultState), nullable=False
    )
    #: Required for an override, null for a confirmation.
    reason_category: Mapped[OverrideReason | None] = mapped_column(
        EnumType(OverrideReason)
    )
    reason_text: Mapped[str | None] = mapped_column(Text)
    #: Evidence the reviewer actually opened, for calibration and audit.
    evidence_viewed: Mapped[list | None] = mapped_column(JSONType)

    __table_args__ = (
        # A confirmation carries no reason; an override must carry both.
        CheckConstraint(
            "(action <> 'override') OR "
            "(reason_category IS NOT NULL AND reason_text IS NOT NULL "
            " AND length(trim(reason_text)) > 0)",
            name="ck_override_requires_reason",
        ),
    )


class SubmissionDecision(Base, TimestampMixin):
    """A submission-level operational decision. Append-only.

    Superseding decisions are new rows; nothing is ever rewritten, so the full
    history of a disputed submission stays intact.
    """

    __tablename__ = "submission_decision"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    submission_version_id: Mapped[str] = mapped_column(
        ForeignKey("submission_version.id"), nullable=False, index=True
    )
    decided_by_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.id"), nullable=False
    )
    decided_by_email: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[DecisionType] = mapped_column(
        EnumType(DecisionType), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    #: What the machine recommended at the time, so agreement and disagreement
    #: with policy are both measurable.
    machine_recommendation: Mapped[str | None] = mapped_column(String)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
