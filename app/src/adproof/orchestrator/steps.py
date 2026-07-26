"""Pipeline steps.

Each step is the body of one job. Steps are chained forward only after the
prior step genuinely succeeded, so no stage can appear to start on the strength
of a stage that failed.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..evaluation.absence import Coverage
from ..evaluation.confidence import band_for_provider_score
from ..evaluation.evaluators import (
    CountedEvidence,
    evaluate_disclosure,
    evaluate_forbidden_occurrence,
    evaluate_max_visual_duration,
    evaluate_min_visual_duration,
    evaluate_required_in_window,
    evaluate_required_spoken_phrase,
    evaluate_sequence,
    evaluate_subjective,
)
from ..models import (
    EvaluationResult,
    EvidenceItem,
    MediaAsset,
    MediaIndex,
    ProcessingJob,
    RetrievalRun,
    Rule,
    Submission,
    SubmissionVersion,
    Workspace,
    utcnow,
)
from ..providers.errors import ProviderError
from ..providers.videodb_adapter import SDK_VERSION, VideoDBAdapter
from ..retrieval.plan import (
    SPOKEN_INDEX_NAME,
    VISUAL_SECONDS_PER_SCENE,
    domains_required,
    plan_for_rule,
    result_threshold_for,
    visual_index_name,
    visual_index_prompt,
)
from ..states import (
    EVALUATOR_VERSION,
    VISUAL_INDEX_PROMPT_VERSION,
    AbsenceClass,
    ConfidenceBand,
    EvidenceOrigin,
    JobState,
    JobType,
    Modality,
    RuleResultState,
    RuleType,
    SubmissionState,
    VisualIndexDomain,
)
from .jobs import enqueue, jobs_for_version

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def dedupe_key(
    submission_version_id: str, job_type: JobType, discriminator: str = ""
) -> str:
    suffix = f":{discriminator}" if discriminator else ""
    return f"{submission_version_id}:{job_type.value}{suffix}"


def _version(session: Session, version_id: str) -> SubmissionVersion:
    return session.get(SubmissionVersion, version_id)


def _submission(session: Session, version: SubmissionVersion) -> Submission:
    return session.get(Submission, version.submission_id)


def _media_asset(session: Session, version_id: str) -> MediaAsset | None:
    return session.scalar(
        select(MediaAsset).where(MediaAsset.submission_version_id == version_id)
    )


def _rules(session: Session, version: SubmissionVersion) -> list[Rule]:
    return list(
        session.scalars(
            select(Rule)
            .where(Rule.rule_set_version_id == version.rule_set_version_id)
            .order_by(Rule.ordinal)
        )
    )


def _set_state(
    session: Session, version: SubmissionVersion, state: SubmissionState,
    error_summary: str | None = None,
) -> None:
    submission = _submission(session, version)
    submission.state = state
    submission.error_summary = error_summary
    session.flush()
    record_audit(
        session,
        workspace_id=submission.workspace_id,
        category=f"submission.{state.value}",
        subject_type="submission",
        subject_id=submission.id,
        detail={"submission_version_id": version.id, "error": error_summary},
    )


# --------------------------------------------------------------------------
# step: ingest
# --------------------------------------------------------------------------


def run_ingest(session: Session, job: ProcessingJob, adapter: VideoDBAdapter) -> str:
    version = _version(session, job.submission_version_id)
    submission = _submission(session, version)
    _set_state(session, version, SubmissionState.ingesting)

    existing = _media_asset(session, version.id)
    if existing and existing.provider_video_id:
        # Already ingested by a prior attempt. Do not re-upload: that would
        # create a duplicate provider asset and duplicate cost.
        logger.info("media already ingested for version %s", version.id)
        return existing.provider_video_id

    workspace = session.get(Workspace, submission.workspace_id)

    media = adapter.ingest(
        source_url=version.source_url,
        source_file_path=version.source_file_path,
        name=f"adproof:{submission.id}:v{version.version}",
        collection_id=workspace.provider_collection_id,
    )

    stream_url = media.stream_url
    if not stream_url:
        stream_url = adapter.generate_stream_url(
            media.provider_video_id, media.provider_collection_id
        )

    asset = existing or MediaAsset(submission_version_id=version.id)
    asset.provider_video_id = media.provider_video_id
    asset.provider_collection_id = media.provider_collection_id
    asset.duration_seconds = media.duration_seconds
    asset.provider_stream_url = stream_url
    asset.provider_player_url = media.player_url
    asset.provider_snapshot = media.snapshot
    asset.sdk_version = SDK_VERSION
    session.add(asset)

    if not workspace.provider_collection_id:
        workspace.provider_collection_id = media.provider_collection_id

    session.flush()
    record_audit(
        session,
        workspace_id=submission.workspace_id,
        category="media.ingested",
        subject_type="media_asset",
        subject_id=asset.id,
        detail={
            "provider_video_id": media.provider_video_id,
            "provider_collection_id": media.provider_collection_id,
            "duration_seconds": media.duration_seconds,
        },
    )
    return media.provider_video_id


def enqueue_indexing(session: Session, version: SubmissionVersion) -> None:
    """Fan out one spoken index job plus one focused visual index per rule."""
    enqueue(
        session,
        submission_version_id=version.id,
        job_type=JobType.index_spoken,
        dedupe_key=dedupe_key(version.id, JobType.index_spoken),
    )
    # One job per focused DOMAIN, deduplicated: five product rules share one
    # product-presence index rather than building five.
    for domain in sorted(domains_required(_rules(session, version))):
        enqueue(
            session,
            submission_version_id=version.id,
            job_type=JobType.index_visual,
            dedupe_key=dedupe_key(
                version.id, JobType.index_visual, visual_index_name(domain)
            ),
        )
    _set_state(session, version, SubmissionState.indexing)


# --------------------------------------------------------------------------
# step: indexing
# --------------------------------------------------------------------------


def run_index_spoken(
    session: Session, job: ProcessingJob, adapter: VideoDBAdapter
) -> str | None:
    version = _version(session, job.submission_version_id)
    asset = _media_asset(session, version.id)
    if not asset or not asset.provider_video_id:
        raise RuntimeError(
            "Cannot index: no ingested media reference exists for this version."
        )

    result = adapter.index_spoken_words(
        asset.provider_video_id, collection_id=asset.provider_collection_id
    )

    index = session.scalar(
        select(MediaIndex).where(
            MediaIndex.media_asset_id == asset.id,
            MediaIndex.index_name == SPOKEN_INDEX_NAME,
        )
    )
    if index is None:
        index = MediaIndex(
            media_asset_id=asset.id,
            modality=Modality.spoken,
            index_name=SPOKEN_INDEX_NAME,
        )
        session.add(index)
    # videodb 0.5.1 index_spoken_words() returns None, so there is no provider
    # index id to record. Left null rather than invented.
    index.provider_index_id = result.provider_index_id
    session.flush()
    return None


def run_index_visual(
    session: Session, job: ProcessingJob, adapter: VideoDBAdapter
) -> str | None:
    version = _version(session, job.submission_version_id)
    asset = _media_asset(session, version.id)
    if not asset or not asset.provider_video_id:
        raise RuntimeError(
            "Cannot index: no ingested media reference exists for this version."
        )

    index_name = job.dedupe_key.rsplit(":", 1)[-1]
    domain = next(
        (d for d in VisualIndexDomain if visual_index_name(d) == index_name), None
    )
    if domain is None:
        raise RuntimeError(
            f"No focused index domain maps to {index_name!r}; refusing to index."
        )

    existing = session.scalar(
        select(MediaIndex).where(
            MediaIndex.media_asset_id == asset.id,
            MediaIndex.index_name == index_name,
        )
    )
    if existing and existing.provider_index_id:
        # Idempotency at our layer: the index already exists, so a retry must
        # not create a second one.
        return existing.provider_index_id

    prompt = visual_index_prompt(domain)
    created = adapter.index_scenes(
        asset.provider_video_id,
        prompt=prompt,
        index_name=index_name,
        seconds_per_scene=VISUAL_SECONDS_PER_SCENE,
        collection_id=asset.provider_collection_id,
    )

    # index_scenes() returns an id before the index is queryable, so the job
    # is not complete until records actually exist. Confirmed against the live
    # API; see docs/VIDEODB_VERIFIED_BEHAVIOR.md C-14.
    record_count = adapter.wait_for_scene_index(
        asset.provider_video_id,
        created.provider_index_id,
        collection_id=asset.provider_collection_id,
    )
    logger.info(
        "visual index %s populated with %d scene record(s)", index_name, record_count
    )

    index = existing or MediaIndex(
        media_asset_id=asset.id,
        modality=Modality.visual,
        index_name=index_name,
    )
    index.provider_index_id = created.provider_index_id
    index.record_count = record_count
    index.prompt = prompt
    index.prompt_version = VISUAL_INDEX_PROMPT_VERSION
    index.extraction_config = {
        "extraction_type": "time",
        "time": VISUAL_SECONDS_PER_SCENE,
        "frame_count": 1,
        "select_frames": ["first"],
    }
    index.measurement_resolution_seconds = VISUAL_SECONDS_PER_SCENE
    session.add(index)
    session.flush()
    return created.provider_index_id


def maybe_enqueue_retrieval(session: Session, version: SubmissionVersion) -> None:
    """Advance to retrieval once every index job has settled.

    Settled includes terminal failure: a rule whose index failed still gets an
    honest result (error), rather than the submission stalling silently.
    """
    index_jobs = [
        j
        for j in jobs_for_version(session, version.id)
        if j.job_type in (JobType.index_spoken, JobType.index_visual)
    ]
    if not index_jobs:
        return
    settled = all(
        j.state in (JobState.succeeded, JobState.failed_terminal) for j in index_jobs
    )
    if not settled:
        return
    enqueue(
        session,
        submission_version_id=version.id,
        job_type=JobType.retrieval,
        dedupe_key=dedupe_key(version.id, JobType.retrieval),
    )
    _set_state(session, version, SubmissionState.evaluating)


# --------------------------------------------------------------------------
# step: retrieval
# --------------------------------------------------------------------------


def _index_state(
    session: Session, version: SubmissionVersion, index_name: str
) -> JobState | None:
    job_type = (
        JobType.index_spoken if index_name == SPOKEN_INDEX_NAME else JobType.index_visual
    )
    discriminator = "" if index_name == SPOKEN_INDEX_NAME else index_name
    key = dedupe_key(version.id, job_type, discriminator)
    job = session.scalar(
        select(ProcessingJob).where(ProcessingJob.dedupe_key == key)
    )
    return job.state if job else None


def run_retrieval(
    session: Session, job: ProcessingJob, adapter: VideoDBAdapter
) -> None:
    version = _version(session, job.submission_version_id)
    submission = _submission(session, version)
    asset = _media_asset(session, version.id)
    if not asset or not asset.provider_video_id:
        raise RuntimeError("Cannot retrieve: no ingested media reference exists.")

    for rule in _rules(session, version):
        if rule.requires_human_review or rule.rule_type is RuleType.subjective_human_review:
            # Subjective rules are never retrieved against or machine-evaluated
            # (PRD s9). No searches are run for them at all.
            continue

        for planned in plan_for_rule(rule):
            index = session.scalar(
                select(MediaIndex).where(
                    MediaIndex.media_asset_id == asset.id,
                    MediaIndex.index_name == planned.index_name,
                )
            )
            index_state = _index_state(session, version, planned.index_name)

            # A fixed cap silently truncates long media, so the effective cap
            # is derived from the media's own duration. See
            # retrieval.plan.result_threshold_for.
            effective_threshold = max(
                planned.result_threshold,
                result_threshold_for(
                    asset.duration_seconds,
                    index.measurement_resolution_seconds
                    if index and index.measurement_resolution_seconds
                    else VISUAL_SECONDS_PER_SCENE,
                ),
            )
            request_params = {
                "query": planned.query,
                "index_type": planned.index_type,
                "search_type": planned.search_type,
                "score_threshold": planned.score_threshold,
                "result_threshold": effective_threshold,
                "scene_index_id": index.provider_index_id if index else None,
                # Routes evidence to the right evaluator argument (the halves of
                # a sequence, the modalities of a disclosure).
                "slot": planned.slot,
                "sdk_version": SDK_VERSION,
            }
            run = RetrievalRun(
                submission_version_id=version.id,
                rule_id=rule.id,
                plan_version=planned.plan_version,
                query=planned.query,
                search_type=planned.search_type,
                index_type=planned.index_type,
                provider_index_id=index.provider_index_id if index else None,
                request_params=request_params,
                counts_toward_measurement=planned.counts_toward_measurement,
                role=planned.role,
                result_threshold=effective_threshold,
            )
            session.add(run)
            session.flush()

            if index_state is not JobState.succeeded:
                # We did not search. result_count stays null (distinct from 0)
                # so nobody can read this as "searched and found nothing".
                run.error_summary = (
                    f"Not executed: index '{planned.index_name}' did not complete "
                    f"(index job state: {index_state.value if index_state else 'missing'})."
                )
                run.finished_at = utcnow()
                session.flush()
                continue

            try:
                shots = adapter.search(
                    asset.provider_video_id,
                    query=planned.query,
                    index_type=planned.index_type,
                    search_type=planned.search_type,
                    score_threshold=planned.score_threshold,
                    result_threshold=effective_threshold,
                    scene_index_id=index.provider_index_id if index else None,
                    collection_id=asset.provider_collection_id,
                )
            except ProviderError as exc:
                # One failed search must not abort the whole retrieval: other
                # rules can still produce honest results.
                run.error_summary = exc.summary
                run.finished_at = utcnow()
                session.flush()
                logger.error("retrieval failed for rule %s: %s", rule.id, exc.summary)
                continue

            for shot in shots:
                session.add(
                    EvidenceItem(
                        retrieval_run_id=run.id,
                        media_asset_id=asset.id,
                        origin=EvidenceOrigin.live_provider,
                        role=planned.role,
                        modality=rule.modality,
                        start_seconds=shot.start_seconds,
                        end_seconds=shot.end_seconds,
                        text=shot.text,
                        provider_score=shot.provider_score,
                        confidence_band=band_for_provider_score(shot.provider_score),
                        provider_index_id=shot.provider_index_id
                        or (index.provider_index_id if index else None),
                        provider_index_name=shot.provider_index_name
                        or planned.index_name,
                        provider_stream_url=shot.stream_url,
                        provider_snapshot=shot.snapshot,
                    )
                )
            run.result_count = len(shots)
            # Saturation means more results may exist, so this run understates
            # the truth. Recorded so the evaluator can refuse to assert a
            # definitive negative from an incomplete evidence set.
            run.result_truncated = len(shots) >= effective_threshold
            if run.result_truncated:
                logger.warning(
                    "retrieval for rule %s saturated at %d results; measurement "
                    "may understate the truth",
                    rule.id,
                    effective_threshold,
                )
            run.finished_at = utcnow()
            session.flush()

    record_audit(
        session,
        workspace_id=submission.workspace_id,
        category="retrieval.completed",
        subject_type="submission_version",
        subject_id=version.id,
        detail={"plan_version": "retrieval-plan/v1"},
    )


# --------------------------------------------------------------------------
# step: evaluation
# --------------------------------------------------------------------------


def _coverage_for_rule(
    session: Session, version: SubmissionVersion, rule: Rule, runs: list[RetrievalRun]
) -> Coverage:
    needed = {p.index_name for p in plan_for_rule(rule) if p.counts_toward_measurement}
    states = {name: _index_state(session, version, name) for name in needed}
    indexes_complete = all(s is JobState.succeeded for s in states.values())
    index_failed = any(s is JobState.failed_terminal for s in states.values())

    counting_runs = [r for r in runs if r.counts_toward_measurement]
    retrieval_failed = any(r.error_summary for r in counting_runs)
    retrieval_attempted = any(
        r.error_summary is None and r.finished_at is not None for r in counting_runs
    )
    return Coverage(
        indexes_complete=indexes_complete,
        index_failed=index_failed,
        retrieval_failed=retrieval_failed,
        retrieval_attempted=retrieval_attempted,
    )


def _visual_resolution(
    session: Session, version: SubmissionVersion, rule: Rule
) -> float | None:
    """Sampling granularity of the index this rule reads from, if visual."""
    from ..retrieval.plan import domain_for

    domain = domain_for(rule)
    if domain is None:
        return None
    index = session.scalar(
        select(MediaIndex)
        .join(MediaAsset, MediaAsset.id == MediaIndex.media_asset_id)
        .where(
            MediaAsset.submission_version_id == version.id,
            MediaIndex.index_name == visual_index_name(domain),
        )
    )
    return index.measurement_resolution_seconds if index else None


def run_evaluation(session: Session, job: ProcessingJob) -> None:
    """Deterministic evaluation.

    No provider access and no language model participate in this step.
    """
    version = _version(session, job.submission_version_id)
    submission = _submission(session, version)

    for rule in _rules(session, version):
        existing = session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.submission_version_id == version.id,
                EvaluationResult.rule_id == rule.id,
            )
        )
        if existing:
            # Results are immutable; a re-run would need a new report version,
            # which is out of scope for this slice.
            continue

        if rule.requires_human_review:
            session.add(
                EvaluationResult(
                    submission_version_id=version.id,
                    rule_id=rule.id,
                    evaluator_version=EVALUATOR_VERSION,
                    state=RuleResultState.human_review_required,
                    absence_class=AbsenceClass.not_applicable,
                    confidence_band=ConfidenceBand.unavailable,
                    explanation=(
                        "This requirement is marked as requiring human judgement. "
                        "AdProof did not search for or evaluate it, and makes no "
                        "machine claim about it."
                    ),
                )
            )
            continue

        runs = list(
            session.scalars(
                select(RetrievalRun).where(
                    RetrievalRun.submission_version_id == version.id,
                    RetrievalRun.rule_id == rule.id,
                )
            )
        )
        counted: list[CountedEvidence] = []
        by_slot: dict[str, list[CountedEvidence]] = {}
        evidence_truncated = False
        for run in runs:
            if not run.counts_toward_measurement:
                continue
            evidence_truncated = evidence_truncated or bool(run.result_truncated)
            slot = (run.request_params or {}).get("slot", "primary")
            for item in session.scalars(
                select(EvidenceItem).where(EvidenceItem.retrieval_run_id == run.id)
            ):
                ev = CountedEvidence(
                    evidence_id=item.id,
                    start_seconds=item.start_seconds,
                    end_seconds=item.end_seconds,
                    provider_score=item.provider_score,
                    text=item.text,
                )
                counted.append(ev)
                by_slot.setdefault(slot, []).append(ev)

        coverage = _coverage_for_rule(session, version, rule, runs)

        window = (
            (rule.window_start_seconds, rule.window_end_seconds)
            if rule.window_start_seconds is not None
            and rule.window_end_seconds is not None
            else None
        )
        resolution = _visual_resolution(session, version, rule)

        match rule.rule_type:
            case RuleType.subjective_human_review:
                outcome = evaluate_subjective(
                    requirement_text=rule.requirement_text,
                    guidance=rule.reviewer_guidance,
                )

            case RuleType.required_spoken_phrase:
                outcome = evaluate_required_spoken_phrase(
                    phrase=rule.phrase,
                    min_occurrences=rule.min_occurrences,
                    evidence=counted,
                    coverage=coverage,
                    absence_policy=rule.absence_policy,
                    evidence_truncated=evidence_truncated,
                )
                resolution = None

            case RuleType.forbidden_spoken_claim:
                outcome = evaluate_forbidden_occurrence(
                    subject=", ".join(rule.forbidden_phrases or []),
                    evidence=counted,
                    coverage=coverage,
                    absence_policy=rule.absence_policy,
                )
                resolution = None

            case RuleType.forbidden_visual_event:
                outcome = evaluate_forbidden_occurrence(
                    subject=rule.visual_concept,
                    evidence=counted,
                    coverage=coverage,
                    absence_policy=rule.absence_policy,
                )

            case RuleType.min_visual_duration:
                outcome = evaluate_min_visual_duration(
                    concept=rule.visual_concept,
                    min_duration_seconds=rule.min_duration_seconds,
                    evidence=counted,
                    coverage=coverage,
                    absence_policy=rule.absence_policy,
                    measurement_resolution_seconds=resolution,
                    evidence_truncated=evidence_truncated,
                )

            case RuleType.max_visual_duration:
                outcome = evaluate_max_visual_duration(
                    concept=rule.visual_concept,
                    max_duration_seconds=rule.max_duration_seconds,
                    evidence=counted,
                    coverage=coverage,
                    absence_policy=rule.absence_policy,
                    measurement_resolution_seconds=resolution,
                    evidence_truncated=evidence_truncated,
                )

            case RuleType.required_visual_event:
                if window:
                    outcome = evaluate_required_in_window(
                        concept=rule.visual_concept,
                        window=window,
                        evidence=counted,
                        coverage=coverage,
                        absence_policy=rule.absence_policy,
                    )
                else:
                    # No window means "at least once", which is the minimum
                    # duration evaluator with a threshold of one sample.
                    outcome = evaluate_min_visual_duration(
                        concept=rule.visual_concept,
                        min_duration_seconds=resolution or 0.1,
                        evidence=counted,
                        coverage=coverage,
                        absence_policy=rule.absence_policy,
                        measurement_resolution_seconds=None,
                        evidence_truncated=evidence_truncated,
                    )

            case RuleType.disclosure_present:
                outcome = evaluate_disclosure(
                    requirement_text=rule.requirement_text,
                    modality_requirement=str(rule.modality_requirement),
                    spoken_evidence=by_slot.get("spoken", []),
                    visual_evidence=by_slot.get("visual", []),
                    coverage=coverage,
                    absence_policy=rule.absence_policy,
                    window=window,
                )
                resolution = None

            case RuleType.sequence:
                outcome = evaluate_sequence(
                    first_concept=rule.sequence_first,
                    second_concept=rule.sequence_second,
                    first_evidence=by_slot.get("first", []),
                    second_evidence=by_slot.get("second", []),
                    coverage=coverage,
                    absence_policy=rule.absence_policy,
                    max_gap_seconds=rule.sequence_max_gap_seconds,
                )
                resolution = None

            case _:
                raise RuntimeError(f"No evaluator for rule type {rule.rule_type!r}")

        session.add(
            EvaluationResult(
                submission_version_id=version.id,
                rule_id=rule.id,
                evaluator_version=outcome.evaluator_version,
                state=outcome.state,
                absence_class=outcome.absence_class,
                measured_value=outcome.measured_value,
                measured_unit=outcome.measured_unit,
                threshold_value=outcome.threshold_value,
                measurement_resolution_seconds=resolution,
                confidence_band=outcome.confidence_band,
                explanation=outcome.explanation,
                measurement_intervals=[
                    list(pair) for pair in outcome.measurement_intervals
                ],
                evidence_ids=outcome.evidence_ids,
            )
        )

    session.flush()

    # A submission is review-ready only when every rule reached a state a
    # reviewer can actually act on. A rule left in `error` or `processing` means
    # part of the analysis never happened, and calling that "ready for review"
    # would overstate completion (VERIFICATION_ENGINE.md s9, USER_JOURNEYS.md
    # journey 2: never show analysis as complete while work is unresolved).
    results = list(
        session.scalars(
            select(EvaluationResult).where(
                EvaluationResult.submission_version_id == version.id
            )
        )
    )
    unresolved = [
        r
        for r in results
        if r.state in (RuleResultState.error, RuleResultState.processing)
    ]
    if unresolved:
        _set_state(
            session,
            version,
            SubmissionState.error,
            error_summary=(
                f"{len(unresolved)} of {len(results)} requirement(s) could not be "
                f"evaluated because required processing did not complete. The "
                f"remaining results are valid; see each requirement for detail."
            ),
        )
    else:
        _set_state(session, version, SubmissionState.ready_for_review)

    record_audit(
        session,
        workspace_id=submission.workspace_id,
        category="evaluation.completed",
        subject_type="submission_version",
        subject_id=version.id,
        detail={
            "evaluator_version": EVALUATOR_VERSION,
            "rules_evaluated": len(results),
            "rules_unresolved": len(unresolved),
        },
    )
