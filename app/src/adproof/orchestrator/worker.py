"""Worker loop.

Runs blocking provider calls out of the request path so the API can report
per-stage state truthfully while work is in flight.

Each job runs in its own transaction. A failure rolls back that job's writes
and records the failure, so a partially-applied step can never masquerade as a
completed one.
"""

from __future__ import annotations

import logging
import signal
import time

from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, init_db
from ..models import ProcessingJob
from ..providers.errors import ProviderError, ProviderNotConfigured
from ..providers.videodb_adapter import VideoDBAdapter
from ..states import JobState, JobType, SubmissionState
from . import steps
from .jobs import claim_next, mark_failed, mark_succeeded

logger = logging.getLogger(__name__)

_running = True


def _stop(_signum, _frame) -> None:
    global _running
    _running = False
    logger.info("shutdown requested; finishing current job")


def _advance(session: Session, job: ProcessingJob) -> None:
    """Chain to the next stage, only after genuine success."""
    version = steps._version(session, job.submission_version_id)
    match job.job_type:
        case JobType.ingest:
            steps.enqueue_indexing(session, version)
        case JobType.index_spoken | JobType.index_visual:
            steps.maybe_enqueue_retrieval(session, version)
        case JobType.retrieval:
            from .jobs import enqueue

            enqueue(
                session,
                submission_version_id=version.id,
                job_type=JobType.evaluation,
                dedupe_key=steps.dedupe_key(version.id, JobType.evaluation),
            )
        case JobType.evaluation:
            pass


def _on_terminal_failure(session: Session, job: ProcessingJob) -> None:
    """Surface a terminal failure at the submission level.

    Indexing failures do NOT stop the pipeline: retrieval and evaluation still
    run so that every rule gets an honest result, including 'error' for the
    rules whose index never completed. Ingestion failure is different -- with no
    media there is nothing to evaluate.
    """
    version = steps._version(session, job.submission_version_id)
    if job.job_type is JobType.ingest:
        steps._set_state(
            session, version, SubmissionState.error, error_summary=job.error_summary
        )
        return
    if job.job_type in (JobType.index_spoken, JobType.index_visual):
        steps.maybe_enqueue_retrieval(session, version)
        return
    steps._set_state(
        session, version, SubmissionState.error, error_summary=job.error_summary
    )


def run_once(adapter: VideoDBAdapter | None = None) -> bool:
    """Claim and run at most one job. Returns True if a job was processed."""
    session = SessionLocal()
    try:
        job = claim_next(session)
        if job is None:
            session.commit()
            return False
        session.commit()  # publish the 'running' state immediately
    except Exception:
        session.rollback()
        session.close()
        raise

    try:
        provider_reference: str | None = None
        if job.job_type is JobType.evaluation:
            steps.run_evaluation(session, job)
        else:
            if adapter is None:
                adapter = VideoDBAdapter()
            match job.job_type:
                case JobType.ingest:
                    provider_reference = steps.run_ingest(session, job, adapter)
                case JobType.index_spoken:
                    provider_reference = steps.run_index_spoken(session, job, adapter)
                case JobType.index_visual:
                    provider_reference = steps.run_index_visual(session, job, adapter)
                case JobType.retrieval:
                    steps.run_retrieval(session, job, adapter)
                case _:
                    raise RuntimeError(f"Unknown job type {job.job_type!r}")

        mark_succeeded(session, job, provider_reference=provider_reference)
        _advance(session, job)
        session.commit()
        logger.info("job %s (%s) succeeded", job.id, job.job_type)
        return True

    except Exception as exc:  # noqa: BLE001 - every failure must be recorded
        session.rollback()
        try:
            job = session.get(ProcessingJob, job.id)
            mark_failed(session, job, exc)
            if job.state is JobState.failed_terminal:
                _on_terminal_failure(session, job)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("could not record job failure for %s", job.id)
        return True
    finally:
        session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    init_db()

    try:
        # Fail loudly at startup rather than accepting jobs we cannot run.
        VideoDBAdapter()
    except ProviderNotConfigured as exc:
        logger.error("%s", exc.summary)
        logger.error(
            "Worker is starting anyway; provider jobs will fail visibly with this "
            "error rather than being simulated."
        )

    logger.info("adproof worker started")
    while _running:
        try:
            did_work = run_once()
        except ProviderError as exc:
            logger.error("provider error in worker loop: %s", exc.summary)
            did_work = False
        if not did_work:
            time.sleep(settings.worker_poll_seconds)
    logger.info("adproof worker stopped")


if __name__ == "__main__":
    main()
