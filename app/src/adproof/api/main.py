"""Application API.

Authentication, workspace authorization, proxied media playback, review
actions, adjudication, analytics and revision drafts are all served here.
Deliberately absent: pagination, webhooks, exports, resubmission. Their absence
is stated in /api/integrity rather than stubbed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import events_for_subject, record_audit
from ..config import settings
from ..db import get_session, init_db
from ..security import hash_password, verify_password
from .auth import (
    Principal,
    authorized_campaign,
    authorized_submission,
    authorize_workspace,
    clear_session_cookie,
    current_principal,
    set_session_cookie,
)
from .media import issue_playback
from .media import router as media_router
from ..evaluation.confidence import derivation_note
from ..models import (
    Campaign,
    Membership,
    EvaluationResult,
    EvidenceItem,
    MediaAsset,
    RetrievalRun,
    Rule,
    RuleReview,
    RuleSetVersion,
    Submission,
    SubmissionDecision,
    SubmissionVersion,
    User,
    Workspace,
    utcnow,
)
from ..orchestrator.jobs import enqueue, jobs_for_version
from ..analytics import RuleOutcome, SubmissionFacts
from ..analytics import compute as analytics_compute
from ..policy import RuleView, adjudicate, state_after
from ..revisions import EvidenceRef, RuleFacts, draft_revisions
from ..orchestrator.steps import dedupe_key
from ..states import (
    CAN_DECIDE_FINAL,
    CAN_MANAGE_CAMPAIGNS,
    CAN_READ,
    CAN_REVIEW,
    CAN_ROUTE,
    CAN_SUBMIT,
    CONFIDENCE_MODEL_VERSION,
    EVALUATOR_VERSION,
    RETRIEVAL_PLAN_VERSION,
    AbsencePolicy,
    DecisionType,
    JobState,
    JobType,
    Modality,
    ModalityRequirement,
    OverrideReason,
    ReviewAction,
    RuleResultState,
    RuleType,
    Severity,
    SubmissionState,
    VisualIndexDomain,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

#: Compared against when an email is unknown, so a failed login costs the same
#: time whether or not the account exists. Prevents user enumeration by timing.
_DUMMY_HASH = hash_password("not-a-real-password-placeholder")

app = FastAPI(
    title="AdProof",
    version="0.1.0-phase1-slice",
    description=(
        "Phase 1 vertical slice. Real VideoDB ingestion, indexing, retrieval, "
        "deterministic evaluation, and timestamped review. No authentication, "
        "no review actions, no fixture data."
    ),
)


app.include_router(media_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------


class LoginInput(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def login(
    payload: LoginInput,
    response: Response,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    user = session.scalar(
        select(User).where(User.email == payload.email.strip().lower())
    )
    # Verify against a dummy hash when the user is unknown so that response
    # timing does not reveal whether an email is registered.
    stored = user.password_hash if user else _DUMMY_HASH
    ok = verify_password(payload.password, stored)
    if not user or not ok or not user.is_active:
        raise HTTPException(401, "Email or password is incorrect.")

    set_session_cookie(response, user.id, secure=settings.cookies_secure)
    memberships = list(
        session.scalars(select(Membership).where(Membership.user_id == user.id))
    )
    return {
        "user": {"id": user.id, "email": user.email, "name": user.display_name},
        "workspaces": [
            {"id": m.workspace_id, "role": m.role} for m in memberships
        ],
    }


@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workspaces = []
    for workspace_id, role in principal.roles.items():
        workspace = session.get(Workspace, workspace_id)
        workspaces.append(
            {
                "id": workspace_id,
                "name": workspace.name if workspace else None,
                "role": role,
            }
        )
    return {
        "user": {
            "id": principal.user.id,
            "email": principal.user.email,
            "name": principal.user.display_name,
        },
        "workspaces": workspaces,
    }


# --------------------------------------------------------------------------
# integrity disclosure
# --------------------------------------------------------------------------


@app.get("/api/integrity")
def integrity() -> dict[str, Any]:
    """What this build does and does not guarantee.

    API_AND_EVENTS.md s7 requires the API to expose whether a report is based
    on live provider processing. This endpoint is that disclosure, and the UI
    renders it rather than hiding it.
    """
    return {
        "evidence_mode": "live_provider_only",
        "fixture_data_present": False,
        "fixture_fallback_on_failure": False,
        "provider_configured": bool(settings.videodb_api_key),
        "versions": {
            "retrieval_plan": RETRIEVAL_PLAN_VERSION,
            "evaluator": EVALUATOR_VERSION,
            "confidence_model": CONFIDENCE_MODEL_VERSION,
        },
        "confidence_disclosure": derivation_note(),
        "known_limitations": [
            "Asynchronous index state is derived from AdProof's own job "
            "records. videodb 0.5.1 blocks internally while polling and "
            "exposes no index-status endpoint, so AdProof does not poll "
            "VideoDB for index progress and reports no percentage.",
            "Confidence bands are uncalibrated and must not be read as "
            "probabilities.",
            "Only required-spoken-phrase and minimum-visual-duration rules "
            "have been validated against real media. The other rule types are "
            "implemented and tested but not yet field-validated.",
            "No evidence reels, exports, or resubmission flow.",
            "Retrieval limits are derived from media duration; when a search "
            "saturates its limit the result is marked as understating the "
            "truth and is never reported as a failure.",
        ],
        "authorization": {
            "authentication": "session cookie, httponly + samesite",
            "workspace_isolation": "enforced on every read and write",
            "review_actions": (
                "confirm, override (reason required), request changes, "
                "escalate, approve, reject"
            ),
            "adjudication": (
                "advisory only; approval is gated on every requirement being "
                "passed or reviewed, and absence alone never recommends "
                "rejection"
            ),
            "media_playback": (
                "proxied through AdProof with a short-lived token bound to the "
                "user and one asset; provider URLs are never returned to the "
                "client"
            ),
        },
    }


# --------------------------------------------------------------------------
# campaign + rule set
# --------------------------------------------------------------------------


class RuleInput(BaseModel):
    """One requirement. Which parameters are required depends on rule_type.

    Validation here mirrors the database CHECK constraints, so a malformed rule
    is rejected with a readable message before it reaches the evaluator.
    """

    rule_type: RuleType
    requirement_text: str
    source_brief_excerpt: str | None = None
    absence_policy: AbsencePolicy = AbsencePolicy.uncertain
    severity: Severity = Severity.required
    requires_human_review: bool = False
    reviewer_guidance: str | None = None

    # spoken
    phrase: str | None = None
    forbidden_phrases: list[str] | None = None
    min_occurrences: int | None = Field(default=None, ge=1)

    # visual
    visual_concept: str | None = None
    visual_domain: VisualIndexDomain | None = None
    min_duration_seconds: float | None = Field(default=None, gt=0)
    max_duration_seconds: float | None = Field(default=None, gt=0)
    #: VideoDB recommends >= 0.3 for scene semantic search to filter
    #: low-relevance noise. Noise here would inflate the measurement.
    score_threshold: float | None = Field(default=0.3, ge=0, le=1)

    # time window
    window_start_seconds: float | None = Field(default=None, ge=0)
    window_end_seconds: float | None = Field(default=None, gt=0)

    # disclosure
    modality_requirement: ModalityRequirement | None = None

    # sequence
    sequence_first: str | None = None
    sequence_second: str | None = None
    sequence_max_gap_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_params(self) -> RuleInput:
        t = self.rule_type
        need = []
        if t is RuleType.required_spoken_phrase:
            if not self.phrase:
                need.append("phrase")
            if self.min_occurrences is None:
                self.min_occurrences = 1
        elif t is RuleType.forbidden_spoken_claim:
            if not self.forbidden_phrases:
                need.append("forbidden_phrases (at least one)")
        elif t is RuleType.min_visual_duration:
            if not self.visual_concept:
                need.append("visual_concept")
            if self.min_duration_seconds is None:
                need.append("min_duration_seconds")
        elif t is RuleType.max_visual_duration:
            if not self.visual_concept:
                need.append("visual_concept")
            if self.max_duration_seconds is None:
                need.append("max_duration_seconds")
        elif t in (RuleType.required_visual_event, RuleType.forbidden_visual_event):
            if not self.visual_concept:
                need.append("visual_concept")
        elif t is RuleType.disclosure_present:
            if self.modality_requirement is None:
                self.modality_requirement = ModalityRequirement.either
        elif t is RuleType.sequence:
            if not self.sequence_first:
                need.append("sequence_first")
            if not self.sequence_second:
                need.append("sequence_second")
        elif t is RuleType.subjective_human_review:
            # Always routed to a person, whatever the caller asked for.
            self.requires_human_review = True

        if need:
            raise ValueError(f"{t.value} requires: {', '.join(need)}")

        if (
            self.window_start_seconds is not None
            and self.window_end_seconds is not None
            and self.window_end_seconds <= self.window_start_seconds
        ):
            raise ValueError("window_end_seconds must be after window_start_seconds")
        return self


_SPOKEN_TYPES = {
    RuleType.required_spoken_phrase,
    RuleType.forbidden_spoken_claim,
}


def _modality_for(rule_type: RuleType) -> Modality:
    """Primary modality of a rule type. Disclosure can be either, so it is
    recorded as spoken only when the rule is spoken-only; otherwise visual."""
    return Modality.spoken if rule_type in _SPOKEN_TYPES else Modality.visual


class CampaignInput(BaseModel):
    #: Optional when the caller belongs to exactly one workspace. Always
    #: validated against the caller's memberships.
    workspace_id: str | None = None
    campaign_name: str
    brief_text: str
    rules: list[RuleInput] = Field(min_length=1)


@app.post("/api/campaigns", status_code=201)
def create_campaign(
    payload: CampaignInput,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create a campaign with an immediately confirmed rule set v1.

    Confirmation is explicit and attributed: `confirmed_by` is required, so no
    rule set becomes active without a named human confirming it
    (PRODUCT_PRINCIPLES.md s4). This slice has no rule editor; rules arrive
    already authored.
    """
    from ..models import BriefVersion

    # The workspace comes from the caller's memberships, never from the request
    # body: accepting a caller-supplied workspace would let anyone write into
    # any tenant.
    workspace_id = payload.workspace_id or (
        principal.workspace_ids[0] if len(principal.workspace_ids) == 1 else None
    )
    if not workspace_id:
        raise HTTPException(
            400,
            "workspace_id is required when you belong to more than one workspace.",
        )
    authorize_workspace(principal, workspace_id, CAN_MANAGE_CAMPAIGNS)
    workspace = session.get(Workspace, workspace_id)

    campaign = Campaign(workspace_id=workspace.id, name=payload.campaign_name)
    session.add(campaign)
    session.flush()

    session.add(
        BriefVersion(
            campaign_id=campaign.id, version=1, original_text=payload.brief_text
        )
    )

    # The confirming human is the authenticated caller, not a self-declared
    # string: an audit trail that anyone can forge is not an audit trail.
    rule_set = RuleSetVersion(
        campaign_id=campaign.id,
        version=1,
        confirmed_at=utcnow(),
        confirmed_by=principal.user.email,
    )
    session.add(rule_set)
    session.flush()

    for ordinal, rule_input in enumerate(payload.rules):
        session.add(
            Rule(
                rule_set_version_id=rule_set.id,
                ordinal=ordinal,
                rule_type=rule_input.rule_type,
                modality=_modality_for(rule_input.rule_type),
                requirement_text=rule_input.requirement_text,
                source_brief_excerpt=rule_input.source_brief_excerpt,
                phrase=rule_input.phrase,
                forbidden_phrases=rule_input.forbidden_phrases,
                min_occurrences=rule_input.min_occurrences,
                visual_concept=rule_input.visual_concept,
                visual_domain=rule_input.visual_domain,
                min_duration_seconds=rule_input.min_duration_seconds,
                max_duration_seconds=rule_input.max_duration_seconds,
                score_threshold=rule_input.score_threshold,
                window_start_seconds=rule_input.window_start_seconds,
                window_end_seconds=rule_input.window_end_seconds,
                modality_requirement=rule_input.modality_requirement,
                sequence_first=rule_input.sequence_first,
                sequence_second=rule_input.sequence_second,
                sequence_max_gap_seconds=rule_input.sequence_max_gap_seconds,
                reviewer_guidance=rule_input.reviewer_guidance,
                absence_policy=rule_input.absence_policy,
                severity=rule_input.severity,
                requires_human_review=rule_input.requires_human_review,
            )
        )

    record_audit(
        session,
        workspace_id=workspace.id,
        category="ruleset.confirmed",
        subject_type="rule_set_version",
        subject_id=rule_set.id,
        actor=principal.user.email,
        detail={"rule_count": len(payload.rules), "version": 1},
    )
    session.commit()
    return {
        "workspace_id": workspace.id,
        "campaign_id": campaign.id,
        "rule_set_version_id": rule_set.id,
    }


@app.get("/api/campaigns")
def list_campaigns(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    out = []
    scoped = select(Campaign).where(
        Campaign.workspace_id.in_(principal.workspace_ids)
    ).order_by(Campaign.created_at)
    for campaign in session.scalars(scoped):
        rule_set = session.scalar(
            select(RuleSetVersion)
            .where(RuleSetVersion.campaign_id == campaign.id)
            .order_by(RuleSetVersion.version.desc())
            .limit(1)
        )
        out.append(
            {
                "id": campaign.id,
                "name": campaign.name,
                "workspace_id": campaign.workspace_id,
                "rule_set_version_id": rule_set.id if rule_set else None,
                "rule_set_version": rule_set.version if rule_set else None,
            }
        )
    return out


# --------------------------------------------------------------------------
# submissions
# --------------------------------------------------------------------------


class SubmissionInput(BaseModel):
    campaign_id: str
    creator_reference: str
    idempotency_key: str
    source_type: Literal["url", "upload"] = "url"
    source_url: str | None = None
    source_file_path: str | None = None

    @model_validator(mode="after")
    def _check_source(self) -> SubmissionInput:
        if bool(self.source_url) == bool(self.source_file_path):
            raise ValueError(
                "Exactly one of source_url or source_file_path must be provided."
            )
        return self


@app.post("/api/submissions", status_code=201)
def create_submission(
    payload: SubmissionInput,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    campaign = authorized_campaign(
        session, principal, payload.campaign_id, CAN_SUBMIT
    )

    rule_set = session.scalar(
        select(RuleSetVersion)
        .where(
            RuleSetVersion.campaign_id == campaign.id,
            RuleSetVersion.confirmed_at.is_not(None),
        )
        .order_by(RuleSetVersion.version.desc())
        .limit(1)
    )
    if rule_set is None:
        raise HTTPException(
            409,
            "This campaign has no confirmed rule set. Rules must be confirmed by a "
            "human before any submission can be evaluated.",
        )

    existing = session.scalar(
        select(Submission).where(
            Submission.workspace_id == campaign.workspace_id,
            Submission.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        # Idempotent replay: return the original, create no new provider work.
        version = session.scalar(
            select(SubmissionVersion)
            .where(SubmissionVersion.submission_id == existing.id)
            .order_by(SubmissionVersion.version.desc())
            .limit(1)
        )
        return {
            "submission_id": existing.id,
            "submission_version_id": version.id if version else None,
            "idempotent_replay": True,
        }

    submission = Submission(
        workspace_id=campaign.workspace_id,
        campaign_id=campaign.id,
        creator_reference=payload.creator_reference,
        state=SubmissionState.draft,
        idempotency_key=payload.idempotency_key,
    )
    session.add(submission)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Idempotency key conflict.") from None

    version = SubmissionVersion(
        submission_id=submission.id,
        version=1,
        rule_set_version_id=rule_set.id,
        source_type=payload.source_type,
        source_url=payload.source_url,
        source_file_path=payload.source_file_path,
        submitted_by=principal.user.email,
    )
    session.add(version)
    session.flush()

    enqueue(
        session,
        submission_version_id=version.id,
        job_type=JobType.ingest,
        dedupe_key=dedupe_key(version.id, JobType.ingest),
    )
    record_audit(
        session,
        workspace_id=submission.workspace_id,
        category="submission.created",
        subject_type="submission",
        subject_id=submission.id,
        actor=principal.user.email,
        detail={"submission_version_id": version.id},
    )
    session.commit()
    return {
        "submission_id": submission.id,
        "submission_version_id": version.id,
        "idempotent_replay": False,
    }


@app.get("/api/submissions")
def list_submissions(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    out = []
    scoped = (
        select(Submission)
        .where(Submission.workspace_id.in_(principal.workspace_ids))
        .order_by(Submission.created_at.desc())
    )
    for submission in session.scalars(scoped):
        campaign = session.get(Campaign, submission.campaign_id)
        out.append(
            {
                "id": submission.id,
                "creator_reference": submission.creator_reference,
                "state": submission.state,
                "campaign_name": campaign.name if campaign else None,
                "created_at": submission.created_at.isoformat(),
            }
        )
    return out


def _stage_view(job) -> dict[str, Any]:
    """One processing stage, reported truthfully.

    No percentage is reported: videodb 0.5.1 gives AdProof no progress signal
    it could honestly relay.
    """
    return {
        "stage": job.job_type,
        "state": job.state,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "elapsed_seconds": job.elapsed_seconds,
        "provider_reference": job.provider_reference,
        "error_summary": job.error_summary,
        "retryable": job.state == JobState.failed_retryable,
        "terminal": job.state in (JobState.succeeded, JobState.failed_terminal),
    }


@app.get("/api/submissions/{submission_id}/report")
def get_report(
    submission_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    submission = authorized_submission(session, principal, submission_id, CAN_READ)

    version = session.scalar(
        select(SubmissionVersion)
        .where(SubmissionVersion.submission_id == submission.id)
        .order_by(SubmissionVersion.version.desc())
        .limit(1)
    )
    if version is None:
        raise HTTPException(409, "Submission has no version.")

    asset = session.scalar(
        select(MediaAsset).where(MediaAsset.submission_version_id == version.id)
    )
    stages = [_stage_view(j) for j in jobs_for_version(session, version.id)]
    all_settled = bool(stages) and all(s["terminal"] for s in stages)

    rules = list(
        session.scalars(
            select(Rule)
            .where(Rule.rule_set_version_id == version.rule_set_version_id)
            .order_by(Rule.ordinal)
        )
    )

    rule_views = []
    for rule in rules:
        result = session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.submission_version_id == version.id,
                EvaluationResult.rule_id == rule.id,
            )
        )
        runs = list(
            session.scalars(
                select(RetrievalRun).where(
                    RetrievalRun.submission_version_id == version.id,
                    RetrievalRun.rule_id == rule.id,
                )
            )
        )
        run_views = []
        evidence_views = []
        for run in runs:
            run_views.append(
                {
                    "id": run.id,
                    "plan_version": run.plan_version,
                    "query": run.query,
                    "search_type": run.search_type,
                    "index_type": run.index_type,
                    "provider_index_id": run.provider_index_id,
                    "role": run.role,
                    "counts_toward_measurement": run.counts_toward_measurement,
                    # null means "did not run"; 0 means "ran and found nothing".
                    "result_count": run.result_count,
                    "result_threshold": run.result_threshold,
                    #: True means more results may exist and the measurement
                    #: understates the truth.
                    "result_truncated": run.result_truncated,
                    "executed": run.error_summary is None and run.finished_at is not None,
                    "error_summary": run.error_summary,
                }
            )
            for item in session.scalars(
                select(EvidenceItem)
                .where(EvidenceItem.retrieval_run_id == run.id)
                .order_by(EvidenceItem.start_seconds)
            ):
                evidence_views.append(
                    {
                        "id": item.id,
                        "role": item.role,
                        "modality": item.modality,
                        "origin": item.origin,
                        "start_seconds": item.start_seconds,
                        "end_seconds": item.end_seconds,
                        "text": item.text,
                        "provider_score": item.provider_score,
                        "confidence_band": item.confidence_band,
                        "counted_toward_measurement": run.counts_toward_measurement,
                        "provenance": {
                            "retrieval_run_id": run.id,
                            "retrieval_query": run.query,
                            "search_type": run.search_type,
                            "index_type": run.index_type,
                            "plan_version": run.plan_version,
                            "provider_index_id": item.provider_index_id,
                            "provider_index_name": item.provider_index_name,
                            "provider_video_id": asset.provider_video_id
                            if asset
                            else None,
                            "sdk_version": asset.sdk_version if asset else None,
                            "created_at": item.created_at.isoformat(),
                        },
                    }
                )

        rule_views.append(
            {
                "id": rule.id,
                "ordinal": rule.ordinal,
                "rule_type": rule.rule_type,
                "modality": rule.modality,
                "requirement_text": rule.requirement_text,
                "source_brief_excerpt": rule.source_brief_excerpt,
                "absence_policy": rule.absence_policy,
                "requires_human_review": rule.requires_human_review,
                # Surfaced because it materially changes which evidence is
                # admitted, and therefore the measured value itself.
                "score_threshold": rule.score_threshold,
                "threshold": (
                    rule.min_duration_seconds
                    if rule.rule_type is RuleType.min_visual_duration
                    else rule.min_occurrences
                ),
                "severity": rule.severity,
                "reviews": [
                    {
                        "id": rv.id,
                        "action": rv.action,
                        "machine_state": rv.machine_state,
                        "human_state": rv.human_state,
                        "reason_category": rv.reason_category,
                        "reason_text": rv.reason_text,
                        "reviewer": rv.reviewer_email,
                        "at": rv.created_at.isoformat(),
                    }
                    for rv in session.scalars(
                        select(RuleReview)
                        .where(
                            RuleReview.submission_version_id == version.id,
                            RuleReview.rule_id == rule.id,
                        )
                        .order_by(RuleReview.created_at)
                    )
                ],
                "result": (
                    {
                        "state": result.state,
                        "absence_class": result.absence_class,
                        "measured_value": result.measured_value,
                        "measured_unit": result.measured_unit,
                        "threshold_value": result.threshold_value,
                        "measurement_resolution_seconds": (
                            result.measurement_resolution_seconds
                        ),
                        "confidence_band": result.confidence_band,
                        "explanation": result.explanation,
                        "measurement_intervals": result.measurement_intervals,
                        "evaluator_version": result.evaluator_version,
                        "source": "machine",
                    }
                    if result
                    else None
                ),
                "retrieval_runs": run_views,
                "evidence": evidence_views,
            }
        )

    gate = adjudicate(
        _rule_views(session, version), processing_complete=all_settled
    )
    decisions = list(
        session.scalars(
            select(SubmissionDecision)
            .where(SubmissionDecision.submission_version_id == version.id)
            .order_by(SubmissionDecision.created_at)
        )
    )

    return {
        "submission": {
            "id": submission.id,
            "state": submission.state,
            "creator_reference": submission.creator_reference,
            "error_summary": submission.error_summary,
        },
        "adjudication": {
            # Advisory only. The permitted set is what a human may actually do.
            "machine_recommendation": (
                gate.recommendation.value if gate.recommendation else None
            ),
            "permitted_decisions": sorted(d.value for d in gate.permitted),
            "blocking_rule_ids": gate.blocking_rule_ids,
            "reasons": gate.reasons,
            "policy_version": gate.policy_version,
        },
        "decisions": [
            {
                "decision": d.decision,
                "rationale": d.rationale,
                "decided_by": d.decided_by_email,
                "machine_recommendation": d.machine_recommendation,
                "agreed_with_machine": d.machine_recommendation == d.decision.value,
                "at": d.created_at.isoformat(),
            }
            for d in decisions
        ],
        "version": {
            "id": version.id,
            "version": version.version,
            "rule_set_version_id": version.rule_set_version_id,
            "source_type": version.source_type,
            "source_url": version.source_url,
        },
        "media": (
            {
                "provider_video_id": asset.provider_video_id,
                "provider_collection_id": asset.provider_collection_id,
                "duration_seconds": asset.duration_seconds,
                "stream_url": asset.provider_stream_url,
                "sdk_version": asset.sdk_version,
            }
            if asset
            else None
        ),
        "stages": stages,
        "processing_complete": all_settled,
        "rules": rule_views,
        "evidence_mode": "live_provider_only",
        "confidence_disclosure": derivation_note(),
    }


@app.post("/api/submissions/{submission_id}/retry")
def retry_failed(
    submission_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Reopen terminally-failed jobs for one more bounded attempt round."""
    submission = authorized_submission(session, principal, submission_id, CAN_SUBMIT)
    version = session.scalar(
        select(SubmissionVersion)
        .where(SubmissionVersion.submission_id == submission.id)
        .order_by(SubmissionVersion.version.desc())
        .limit(1)
    )
    reopened = 0
    for job in jobs_for_version(session, version.id):
        if job.state is JobState.failed_terminal:
            job.max_attempts = job.attempt_count + settings.max_job_attempts
            job.state = JobState.queued
            job.error_summary = None
            job.finished_at = None
            reopened += 1
    if reopened:
        submission.state = SubmissionState.ingesting
        submission.error_summary = None
        record_audit(
            session,
            workspace_id=submission.workspace_id,
            category="processing.retry_requested",
            subject_type="submission",
            subject_id=submission.id,
            actor=principal.user.email,
            detail={"jobs_reopened": reopened},
        )
    session.commit()
    return {"jobs_reopened": reopened}


@app.get("/api/submissions/{submission_id}/audit")
def get_audit(
    submission_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    authorized_submission(session, principal, submission_id, CAN_READ)
    events = events_for_subject(session, "submission", submission_id)
    version = session.scalar(
        select(SubmissionVersion)
        .where(SubmissionVersion.submission_id == submission_id)
        .order_by(SubmissionVersion.version.desc())
        .limit(1)
    )
    if version:
        events += events_for_subject(session, "submission_version", version.id)
    events.sort(key=lambda e: e.created_at)
    return [
        {
            "at": e.created_at.isoformat(),
            "category": e.category,
            "actor": e.actor,
            "subject_type": e.subject_type,
            "detail": e.detail,
        }
        for e in events
    ]


@app.get("/api/evidence/{evidence_id}/playback")
def evidence_playback(
    evidence_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Authorized playback reference for one evidence item.

    Returns a URL into AdProof's own media proxy, never the provider URL.
    VideoDB stream URLs were VERIFIED to be publicly fetchable with no
    credential, so returning one would leak unpublished creator media to
    anyone who obtained the link.
    """
    item = session.get(EvidenceItem, evidence_id)
    if item is None:
        raise HTTPException(404, "Evidence not found")

    # Authorization is resolved through the asset's owning workspace, so an
    # evidence id from another tenant resolves to 404, not to media.
    playback = issue_playback(session, principal, item.media_asset_id)

    asset = session.get(MediaAsset, item.media_asset_id)
    if not asset or not asset.provider_stream_url:
        raise HTTPException(
            409,
            "No playable stream is recorded for this evidence. The media may not "
            "have finished ingesting.",
        )
    return {
        **playback,
        "seek_to_seconds": item.start_seconds,
        "end_seconds": item.end_seconds,
    }


# --------------------------------------------------------------------------
# static UI
# --------------------------------------------------------------------------

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def landing() -> FileResponse:
        """Public marketing page. Static, unauthenticated, no data access."""
        return FileResponse(WEB_DIR / "landing.html")

    @app.get("/app")
    def review_app() -> FileResponse:
        """The reviewer application shell.

        Served to anyone, but it renders only a login form until a session
        exists: every data endpoint it calls requires authentication.

        no-cache so a stale shell can never reference stale assets: a cached
        HTML + cached CSS pair is exactly what made every view render stacked
        on one page.
        """
        return FileResponse(
            WEB_DIR / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )


# --------------------------------------------------------------------------
# review workspace
# --------------------------------------------------------------------------


def _rule_views(session: Session, version: SubmissionVersion) -> list[RuleView]:
    """Assemble each rule's machine result and any human position on it."""
    views: list[RuleView] = []
    rules = session.scalars(
        select(Rule)
        .where(Rule.rule_set_version_id == version.rule_set_version_id)
        .order_by(Rule.ordinal)
    )
    for rule in rules:
        result = session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.submission_version_id == version.id,
                EvaluationResult.rule_id == rule.id,
            )
        )
        # The latest review wins for the effective state, but every prior
        # review remains on record.
        latest = session.scalar(
            select(RuleReview)
            .where(
                RuleReview.submission_version_id == version.id,
                RuleReview.rule_id == rule.id,
            )
            .order_by(RuleReview.created_at.desc())
            .limit(1)
        )
        views.append(
            RuleView(
                rule_id=rule.id,
                requirement_text=rule.requirement_text,
                severity=rule.severity,
                machine_state=result.state if result else None,
                absence_class=result.absence_class if result else None,
                human_state=latest.human_state if latest else None,
            )
        )
    return views


def _latest_version(session: Session, submission: Submission) -> SubmissionVersion:
    version = session.scalar(
        select(SubmissionVersion)
        .where(SubmissionVersion.submission_id == submission.id)
        .order_by(SubmissionVersion.version.desc())
        .limit(1)
    )
    if version is None:
        raise HTTPException(409, "Submission has no version.")
    return version


def _processing_complete(session: Session, version: SubmissionVersion) -> bool:
    jobs = jobs_for_version(session, version.id)
    return bool(jobs) and all(
        j.state in (JobState.succeeded, JobState.failed_terminal) for j in jobs
    )


class RuleReviewInput(BaseModel):
    action: ReviewAction
    #: Required for an override; ignored for a confirmation.
    human_state: RuleResultState | None = None
    reason_category: OverrideReason | None = None
    reason_text: str | None = None
    evidence_viewed: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> RuleReviewInput:
        if self.action is ReviewAction.override:
            if self.human_state is None:
                raise ValueError("human_state is required when overriding.")
            if self.reason_category is None:
                raise ValueError("reason_category is required when overriding.")
            if not (self.reason_text or "").strip():
                raise ValueError("reason_text is required when overriding.")
            if self.human_state in (
                RuleResultState.processing,
                RuleResultState.not_evaluated,
            ):
                raise ValueError(
                    "A reviewer cannot set a rule back to a processing state."
                )
        return self


@app.post("/api/submissions/{submission_id}/rules/{rule_id}/review", status_code=201)
def review_rule(
    submission_id: str,
    rule_id: str,
    payload: RuleReviewInput,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Record a human position on one rule.

    The machine result is never modified. This appends a review alongside it,
    carrying a snapshot of the machine state so the record stays interpretable
    even if the report is re-evaluated later.
    """
    submission = authorized_submission(session, principal, submission_id, CAN_REVIEW)
    version = _latest_version(session, submission)

    rule = session.get(Rule, rule_id)
    if rule is None or rule.rule_set_version_id != version.rule_set_version_id:
        raise HTTPException(404, "Rule not found on this submission.")

    result = session.scalar(
        select(EvaluationResult).where(
            EvaluationResult.submission_version_id == version.id,
            EvaluationResult.rule_id == rule.id,
        )
    )
    if result is None:
        raise HTTPException(
            409,
            "This requirement has no machine result yet. There is nothing to "
            "confirm or override until evaluation has run.",
        )

    human_state = (
        result.state if payload.action is ReviewAction.confirm else payload.human_state
    )
    review = RuleReview(
        submission_version_id=version.id,
        rule_id=rule.id,
        evaluation_result_id=result.id,
        reviewer_id=principal.user.id,
        reviewer_email=principal.user.email,
        action=payload.action,
        machine_state=result.state,
        human_state=human_state,
        reason_category=payload.reason_category,
        reason_text=payload.reason_text,
        evidence_viewed=payload.evidence_viewed,
    )
    session.add(review)
    session.flush()

    record_audit(
        session,
        workspace_id=submission.workspace_id,
        category=(
            "review.rule_confirmed"
            if payload.action is ReviewAction.confirm
            else "review.rule_overridden"
        ),
        subject_type="submission_version",
        subject_id=version.id,
        actor=principal.user.email,
        detail={
            "rule_id": rule.id,
            "machine_state": result.state.value,
            "human_state": human_state.value,
            "reason_category": (
                payload.reason_category.value if payload.reason_category else None
            ),
        },
    )
    session.commit()
    return {
        "review_id": review.id,
        "machine_state": result.state,
        "human_state": human_state,
    }


class DecisionInput(BaseModel):
    decision: DecisionType
    rationale: str = Field(min_length=1)


@app.post("/api/submissions/{submission_id}/decision", status_code=201)
def decide(
    submission_id: str,
    payload: DecisionInput,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Record a submission-level decision, subject to the adjudication gate."""
    # Final approve/reject is a campaign-manager action; a reviewer may still
    # request changes or escalate (DATA_MODEL.md role model).
    required = (
        CAN_DECIDE_FINAL
        if payload.decision in (DecisionType.approve, DecisionType.reject)
        else CAN_ROUTE
    )
    submission = authorized_submission(session, principal, submission_id, required)
    version = _latest_version(session, submission)

    gate = adjudicate(
        _rule_views(session, version),
        processing_complete=_processing_complete(session, version),
    )
    if payload.decision not in gate.permitted:
        raise HTTPException(
            409,
            "That decision is not available yet: "
            + " ".join(gate.reasons or ["unresolved requirements remain."]),
        )

    decision = SubmissionDecision(
        submission_version_id=version.id,
        decided_by_id=principal.user.id,
        decided_by_email=principal.user.email,
        decision=payload.decision,
        rationale=payload.rationale,
        machine_recommendation=(
            gate.recommendation.value if gate.recommendation else None
        ),
        policy_version=gate.policy_version,
    )
    session.add(decision)

    submission.state = state_after(payload.decision)
    record_audit(
        session,
        workspace_id=submission.workspace_id,
        category=f"decision.{payload.decision.value}",
        subject_type="submission",
        subject_id=submission.id,
        actor=principal.user.email,
        detail={
            "machine_recommendation": decision.machine_recommendation,
            "policy_version": gate.policy_version,
            "agreed_with_machine": decision.machine_recommendation
            == payload.decision.value,
        },
    )
    session.commit()
    return {
        "decision_id": decision.id,
        "submission_state": submission.state,
        "machine_recommendation": decision.machine_recommendation,
    }


# --------------------------------------------------------------------------
# revision instructions (Journey 4)
# --------------------------------------------------------------------------


@app.get("/api/submissions/{submission_id}/revision-draft")
def revision_draft(
    submission_id: str,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Draft revision instructions grounded in the rule and the evidence.

    A draft only. The reviewer edits and approves it before anything is sent
    (Journey 4 step 3).
    """
    submission = authorized_submission(session, principal, submission_id, CAN_READ)
    version = _latest_version(session, submission)

    facts: list[RuleFacts] = []
    for rule in session.scalars(
        select(Rule)
        .where(Rule.rule_set_version_id == version.rule_set_version_id)
        .order_by(Rule.ordinal)
    ):
        result = session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.submission_version_id == version.id,
                EvaluationResult.rule_id == rule.id,
            )
        )
        if result is None:
            continue
        # Only evidence that counted toward the measurement may be cited, so a
        # draft cannot point at a moment the evaluator deliberately excluded.
        counted = [
            EvidenceRef(start_seconds=item.start_seconds, end_seconds=item.end_seconds)
            for run in session.scalars(
                select(RetrievalRun).where(
                    RetrievalRun.submission_version_id == version.id,
                    RetrievalRun.rule_id == rule.id,
                    RetrievalRun.counts_toward_measurement.is_(True),
                )
            )
            for item in session.scalars(
                select(EvidenceItem)
                .where(EvidenceItem.retrieval_run_id == run.id)
                .order_by(EvidenceItem.start_seconds)
            )
        ]
        facts.append(
            RuleFacts(
                rule_id=rule.id,
                rule_type=rule.rule_type,
                requirement_text=rule.requirement_text,
                severity=rule.severity,
                state=result.state,
                absence_class=result.absence_class,
                measured_value=result.measured_value,
                measured_unit=result.measured_unit,
                threshold_value=result.threshold_value,
                measurement_resolution_seconds=result.measurement_resolution_seconds,
                evidence=counted,
                phrase=rule.phrase,
                visual_concept=rule.visual_concept,
            )
        )

    draft = draft_revisions(facts)
    return {
        "message": draft.message,
        "items": [
            {
                "rule_id": i.rule_id,
                "requirement_text": i.requirement_text,
                "severity": i.severity,
                "instruction": i.instruction,
                "basis": i.basis,
                "is_query_not_assertion": i.is_query_not_assertion,
            }
            for i in draft.items
        ],
        "excluded": [
            {"rule_id": rid, "reason": reason} for rid, reason in draft.excluded
        ],
        "note": (
            "Draft only. Edit before sending. Instructions never assert that "
            "something is absent, because a search miss is not proof."
        ),
    }


# --------------------------------------------------------------------------
# campaign analytics (Journey 5)
# --------------------------------------------------------------------------


@app.get("/api/campaigns/{campaign_id}/analytics")
def campaign_analytics(
    campaign_id: str,
    include_incomplete: bool = False,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Aggregate a campaign's submissions.

    Incomplete work is excluded unless explicitly requested, machine and human
    outcomes stay separate, and every count carries its submission ids.
    """
    campaign = authorized_campaign(session, principal, campaign_id, CAN_READ)

    facts: list[SubmissionFacts] = []
    for submission in session.scalars(
        select(Submission).where(Submission.campaign_id == campaign.id)
    ):
        version = session.scalar(
            select(SubmissionVersion)
            .where(SubmissionVersion.submission_id == submission.id)
            .order_by(SubmissionVersion.version.desc())
            .limit(1)
        )
        if version is None:
            continue

        outcomes: list[RuleOutcome] = []
        for rule in session.scalars(
            select(Rule)
            .where(Rule.rule_set_version_id == version.rule_set_version_id)
            .order_by(Rule.ordinal)
        ):
            result = session.scalar(
                select(EvaluationResult).where(
                    EvaluationResult.submission_version_id == version.id,
                    EvaluationResult.rule_id == rule.id,
                )
            )
            review = session.scalar(
                select(RuleReview)
                .where(
                    RuleReview.submission_version_id == version.id,
                    RuleReview.rule_id == rule.id,
                )
                .order_by(RuleReview.created_at.desc())
                .limit(1)
            )
            outcomes.append(
                RuleOutcome(
                    rule_id=rule.id,
                    requirement_text=rule.requirement_text,
                    machine_state=result.state if result else None,
                    human_state=review.human_state if review else None,
                    override_reason=review.reason_category if review else None,
                )
            )

        decision = session.scalar(
            select(SubmissionDecision)
            .where(SubmissionDecision.submission_version_id == version.id)
            .order_by(SubmissionDecision.created_at.desc())
            .limit(1)
        )
        jobs = jobs_for_version(session, version.id)
        facts.append(
            SubmissionFacts(
                submission_id=submission.id,
                creator_reference=submission.creator_reference,
                state=submission.state,
                rules=outcomes,
                final_decision=decision.decision if decision else None,
                time_to_decision_seconds=(
                    (decision.created_at - submission.created_at).total_seconds()
                    if decision
                    else None
                ),
                has_processing_error=any(
                    j.state is JobState.failed_terminal for j in jobs
                ),
            )
        )

    a = analytics_compute(facts, include_incomplete=include_incomplete)

    def tally(t) -> dict[str, Any]:
        return {"count": t.count, "submission_ids": t.submission_ids}

    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "include_incomplete": include_incomplete,
        "totals": {
            "submissions": a.total_submissions,
            "included": tally(a.included_submissions),
            "excluded_incomplete": tally(a.excluded_incomplete),
            "processing_errors": tally(a.processing_errors),
            "review_ready": tally(a.review_ready),
            "unresolved": tally(a.unresolved),
        },
        "machine_pass_rate": a.machine_pass_rate,
        "final_approval_rate": a.final_approval_rate,
        "failure_patterns": [
            {
                "rule_id": p.rule_id,
                "requirement_text": p.requirement_text,
                "machine_failures": tally(p.machine_failures),
                "human_confirmed_failures": tally(p.human_confirmed_failures),
                "overridden_away": tally(p.overridden_away),
                "override_reasons": p.override_reasons,
            }
            for p in a.failure_patterns
        ],
        "creator_trends": [
            {
                "creator_reference": t.creator_reference,
                "submissions": tally(t.submissions),
                "repeated_failures": t.repeated_failures,
            }
            for t in a.creator_trends
        ],
        "override_rate": a.override_rate,
        "override_reason_totals": a.override_reason_totals,
        "median_time_to_decision_seconds": a.median_time_to_decision_seconds,
        "unavailable": a.unavailable,
    }
