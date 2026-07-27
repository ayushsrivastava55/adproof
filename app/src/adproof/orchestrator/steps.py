"""Pipeline steps.

Each step is the body of one job. Steps are chained forward only after the
prior step genuinely succeeded, so no stage can appear to start on the strength
of a stage that failed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from videodb import IndexType

from ..audit import record_audit
from ..evaluation.absence import Coverage
from ..evaluation.confidence import band_for_provider_score
from ..evaluation.evaluators import (
    CountedEvidence,
    filter_action_evidence,
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
from ..retrieval.qualify import (
    CONTRADICTS,
    QUALIFIER_VERSION,
    UNSURE,
    qualify_texts,
)
from ..retrieval import verdict as verdict_layer
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

            # Qualify counted VISUAL evidence: a model reads each description
            # against the rule's concept and answers supports / contradicts /
            # unsure. Ranking alone put a rocket above a real cereal box and
            # passed 36.8s of an untouched package as "product use"; the
            # verdict is what separates similarity from presence. Exact keyword
            # hits and disclosure-marker evidence are not qualified: they are
            # already deterministic.
            verdicts = [None] * len(shots)
            if (
                shots
                and planned.counts_toward_measurement
                and planned.index_type == IndexType.scene
                and rule.rule_type is not RuleType.disclosure_present
            ):
                # The qualification question must match what the RULE
                # measures, not the retrieval query. Search phrasing is
                # deliberately narrow for recall ("being held up and shown to
                # the camera"); asking the qualifier that same question failed
                # a correct 42.8s visibility pass because the package was
                # merely sitting in frame. Visibility rules ask visibility;
                # event rules ask whether the event is happening.
                if rule.rule_type in (
                    RuleType.min_visual_duration,
                    RuleType.max_visual_duration,
                    RuleType.forbidden_visual_event,
                ):
                    concept = (
                        f"{rule.visual_concept} -- or clearly the same product "
                        f"or object -- is VISIBLE in the frame. Visibility is "
                        f"enough; it does not need to be held or in use."
                    )
                else:
                    concept = (
                        f"{rule.visual_concept} is actually HAPPENING in the "
                        f"frame, not merely possible."
                    )
                try:
                    verdicts = qualify_texts(
                        adapter,
                        concept,
                        [shot.text or "" for shot in shots],
                        collection_id=asset.provider_collection_id,
                    )
                except ProviderError as exc:
                    # Qualification failing must fail the run visibly. Skipping
                    # it would readmit exactly the false positives it exists to
                    # stop, silently.
                    run.error_summary = (
                        f"Evidence qualification failed: {exc.summary}"
                    )
                    run.finished_at = utcnow()
                    session.flush()
                    logger.error(
                        "qualification failed for rule %s: %s", rule.id, exc.summary
                    )
                    continue

            for shot, verdict in zip(shots, verdicts):
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
                        qualification=verdict,
                        qualifier_version=(
                            QUALIFIER_VERSION if verdict is not None else None
                        ),
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


#: Rule types whose outcome turns on reading a description rather than on
#: counting an exact token. Spoken-phrase rules stay purely deterministic:
#: "did the transcript contain this phrase N times" is arithmetic, and handing
#: it to a language model would only add a way to get it wrong.
_INTERPRETIVE_RULE_TYPES = frozenset({
    RuleType.min_visual_duration,
    RuleType.max_visual_duration,
    RuleType.required_visual_event,
    RuleType.forbidden_visual_event,
    RuleType.disclosure_present,
    RuleType.sequence,
})

_VERDICT_STATES = {
    "pass": RuleResultState.passed,
    "fail": RuleResultState.failed,
    "uncertain": RuleResultState.uncertain,
}


@dataclass(frozen=True)
class _ReviewedOutcome:
    """The result after both layers have had their say."""

    state: RuleResultState
    confidence_band: ConfidenceBand
    explanation: str
    decided_by: str
    model: str | None = None
    prompt_version: str | None = None
    reasoning: str | None = None


def _measurement_summary(outcome) -> str:
    """State the deterministic finding as a fact for the reading model.

    The model is given the number; it is never asked to produce one.
    """
    if outcome.measured_value is None:
        return "No measurement applies to this rule type."
    unit = outcome.measured_unit or ""
    text = f"measured {outcome.measured_value:g} {unit}".strip()
    if outcome.threshold_value is not None:
        text += f", against a threshold of {outcome.threshold_value:g} {unit}".strip()
    return (
        f"Deterministic code {text}. It counted only descriptions that passed "
        f"qualification, so this number can understate reality if qualification "
        f"was wrong. Its own conclusion was '{outcome.state.value}'."
    )


def _evidence_lines(items, role, offset=0):
    return [
        verdict_layer.EvidenceLine(
            index=offset + n,
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
            text=item.text,
            role=role,
        )
        for n, item in enumerate(items, start=1)
    ]


def _apply_verdict_model(*, rule, outcome, supporting, conflicting, adapter=None):
    """Let a reading model decide interpretive rules from the descriptions.

    The deterministic measurement is computed first and passed in as a fact.
    The model reads the supporting and conflicting descriptions and returns its
    own verdict. Both conclusions are stored; where they disagree, the
    disagreement is stated in the explanation and confidence drops, because two
    layers reaching different answers is exactly the situation a reviewer needs
    to see rather than have resolved silently.

    Deliberate limits:
      * purely arithmetic rules never reach this function;
      * if the model is unreachable the deterministic result stands and the
        result records that no model participated -- there is no pretence that
        a reading happened;
      * an unparseable answer becomes `uncertain`, never a pass.
    """
    deterministic = _ReviewedOutcome(
        state=outcome.state,
        confidence_band=outcome.confidence_band,
        explanation=outcome.explanation,
        decided_by="deterministic",
    )

    if rule.rule_type not in _INTERPRETIVE_RULE_TYPES:
        return deterministic
    if not verdict_layer.is_configured(adapter):
        return deterministic
    if not supporting and not conflicting:
        # Nothing was retrieved at all. There is no text to read, so the
        # absence policy the deterministic evaluator already applied is the
        # honest answer; asking a model to opine on an empty page is not.
        return deterministic

    lines = _evidence_lines(supporting, "supporting")
    lines += _evidence_lines(conflicting, "conflicting", offset=len(supporting))

    try:
        result = verdict_layer.get_verdict(
            requirement=rule.requirement_text,
            measurement=_measurement_summary(outcome),
            supporting=[line for line in lines if line.role == "supporting"],
            conflicting=[line for line in lines if line.role == "conflicting"],
            adapter=adapter,
        )
    except verdict_layer.VerdictUnavailable as exc:
        logger.warning("verdict model unavailable for rule %s: %s", rule.id, exc)
        return _ReviewedOutcome(
            state=outcome.state,
            confidence_band=outcome.confidence_band,
            explanation=(
                f"{outcome.explanation}\n\nThe reading model was not available "
                f"for this result ({exc}), so this is the deterministic "
                f"measurement alone."
            ),
            decided_by="deterministic",
        )

    state = _VERDICT_STATES[result.state]

    if (
        outcome.state is RuleResultState.human_review_required
        and state is RuleResultState.passed
    ):
        # A rule the evaluator escalated may not be cleared by a model reading
        # the same descriptions the evaluator already found insufficient. The
        # verdict is kept, but capped at `uncertain`, which still routes the
        # submission back to the creator rather than approving it.
        state = RuleResultState.uncertain

    agrees = state is outcome.state

    if agrees:
        explanation = (
            f"{outcome.explanation}\n\nA model read the retrieved descriptions "
            f"and reached the same conclusion: {result.reasoning}"
        )
        band = outcome.confidence_band
    else:
        explanation = (
            f"A model read the retrieved descriptions and concluded "
            f"'{state.value}': {result.reasoning}\n\n"
            f"The deterministic measurement concluded "
            f"'{outcome.state.value}' instead. {outcome.explanation}\n\n"
            f"The two layers disagree, so confidence in this result is low."
        )
        # Disagreement is a genuine loss of certainty and is recorded as one.
        band = ConfidenceBand.low

    return _ReviewedOutcome(
        state=state,
        confidence_band=band,
        explanation=explanation,
        decided_by="verdict_model",
        model=result.model,
        prompt_version=result.prompt_version,
        reasoning=result.reasoning,
    )


def run_evaluation(
    session: Session, job: ProcessingJob, adapter: VideoDBAdapter | None = None
) -> None:
    """Evaluation: deterministic measurement, then a reading model verdict.

    Every number -- durations, occurrence counts, merged intervals, threshold
    comparisons -- is computed here by deterministic code and is never asked of
    a model. What a model does contribute, for the rule types where the answer
    turns on interpreting a scene description rather than on arithmetic, is a
    reading of the supporting and conflicting descriptions. Both conclusions
    are stored separately (see `_apply_verdict_model`).
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
        #: Everything retrieved for this rule, kept so the reading model sees
        #: both sides -- including the descriptions the qualifier discarded,
        #: which are precisely the ones that carry a contradiction.
        conflicting: list[CountedEvidence] = []
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
                if item.qualification in (CONTRADICTS, UNSURE):
                    # Qualified out: the description itself does not depict the
                    # requirement. It may not contribute to any measurement,
                    # but the reading model still gets to see it.
                    conflicting.append(ev)
                    continue
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
                # A frame whose description denies any action never counts,
                # whatever its similarity score (see filter_action_evidence).
                counted = filter_action_evidence(counted)
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

        review = _apply_verdict_model(
            rule=rule, outcome=outcome, supporting=counted,
            conflicting=conflicting, adapter=adapter,
        )

        session.add(
            EvaluationResult(
                submission_version_id=version.id,
                rule_id=rule.id,
                evaluator_version=outcome.evaluator_version,
                state=review.state,
                decided_by=review.decided_by,
                deterministic_state=outcome.state,
                verdict_model=review.model,
                verdict_prompt_version=review.prompt_version,
                verdict_reasoning=review.reasoning,
                absence_class=outcome.absence_class,
                measured_value=outcome.measured_value,
                measured_unit=outcome.measured_unit,
                threshold_value=outcome.threshold_value,
                measurement_resolution_seconds=resolution,
                confidence_band=review.confidence_band,
                explanation=review.explanation,
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

    _maybe_auto_decide(session, version, submission)

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


def _maybe_auto_decide(session, version, submission) -> None:
    """Execute the policy recommendation when the campaign opted in.

    Triage, not judgement: a submission with a clear recommendation is decided
    by policy and recorded as such; anything without one stays in the human
    exception queue. The actor is `policy:auto`, so automated decisions remain
    permanently distinguishable from human ones, and the same append-only
    decision record is written either way.
    """
    from ..models import Campaign, SubmissionDecision
    from ..policy import RuleView, adjudicate, state_after

    campaign = session.get(Campaign, submission.campaign_id)
    if not campaign or not campaign.auto_decide:
        return
    views = []
    for rule in _rules(session, version):
        result = session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.submission_version_id == version.id,
                EvaluationResult.rule_id == rule.id,
            )
        )
        views.append(RuleView(
            rule_id=rule.id,
            requirement_text=rule.requirement_text,
            severity=rule.severity,
            machine_state=result.state if result else None,
            absence_class=result.absence_class if result else None,
        ))
    gate = adjudicate(views, processing_complete=True)

    from ..states import DecisionType as DT

    decision_type = gate.recommendation
    rationale = " ".join(gate.reasons)[:1500] or "Automated policy decision."
    if decision_type is DT.request_changes:
        # The rationale is what the creator reads. Send the evidence-grounded
        # draft, not internal policy reasoning written for reviewers.
        from ..revisions import draft_revisions

        rationale = ("AUTOMATED: " + draft_revisions(
            _revision_facts(session, version)
        ).message)[:2000]
    if decision_type is None:
        # Fully automated mode: uncertainty is not parked for a reviewer, it is
        # bounced to the creator with the evidence-grounded revision draft. The
        # creator resolves it by fixing or resubmitting; no internal human is
        # in the loop. Never converted into approve or reject: an unverified
        # requirement is not a verdict in either direction.
        from ..revisions import draft_revisions

        decision_type = DT.request_changes
        facts = _revision_facts(session, version)
        rationale = (
            "AUTOMATED: could not verify every requirement. "
            + draft_revisions(facts).message
        )[:2000]

    decision = SubmissionDecision(
        submission_version_id=version.id,
        decided_by_id=None,
        decided_by_email="policy:auto",
        decision=decision_type,
        rationale=rationale,
        machine_recommendation=(
            gate.recommendation.value if gate.recommendation else None
        ),
        policy_version=gate.policy_version,
    )
    session.add(decision)
    submission.state = state_after(decision_type)
    session.flush()
    record_audit(
        session,
        workspace_id=submission.workspace_id,
        category=f"decision.auto.{decision_type.value}",
        subject_type="submission",
        subject_id=submission.id,
        actor="policy:auto",
        detail={"policy_version": gate.policy_version, "reasons": gate.reasons},
    )


def _revision_facts(session, version):
    """RuleFacts for the auto revision draft (mirrors the API endpoint)."""
    from ..revisions import EvidenceRef, RuleFacts

    facts = []
    for rule in _rules(session, version):
        result = session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.submission_version_id == version.id,
                EvaluationResult.rule_id == rule.id,
            )
        )
        if result is None:
            continue
        counted = [
            EvidenceRef(start_seconds=i.start_seconds, end_seconds=i.end_seconds)
            for run in session.scalars(
                select(RetrievalRun).where(
                    RetrievalRun.submission_version_id == version.id,
                    RetrievalRun.rule_id == rule.id,
                    RetrievalRun.counts_toward_measurement.is_(True),
                )
            )
            for i in session.scalars(
                select(EvidenceItem).where(EvidenceItem.retrieval_run_id == run.id)
            )
        ]
        facts.append(RuleFacts(
            rule_id=rule.id, rule_type=rule.rule_type,
            requirement_text=rule.requirement_text, severity=rule.severity,
            state=result.state, absence_class=result.absence_class,
            measured_value=result.measured_value, measured_unit=result.measured_unit,
            threshold_value=result.threshold_value,
            measurement_resolution_seconds=result.measurement_resolution_seconds,
            evidence=counted, phrase=rule.phrase, visual_concept=rule.visual_concept,
        ))
    return facts
