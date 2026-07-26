"""Durable job queue over Postgres.

The job row is the authority on asynchronous state. videodb 0.5.1 blocks
internally while polling and exposes no index-status endpoint, so AdProof does
not claim to poll VideoDB; it records the state of its own attempt to run the
blocking call. That distinction is stated in the UI.
"""

from __future__ import annotations

import logging
import traceback

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ProcessingJob, utcnow
from ..providers.errors import ProviderError
from ..states import JobState, JobType

logger = logging.getLogger(__name__)


def enqueue(
    session: Session,
    *,
    submission_version_id: str,
    job_type: JobType,
    dedupe_key: str,
) -> ProcessingJob:
    """Create a job, or return the existing one with the same dedupe key.

    Idempotent by construction: the unique constraint on dedupe_key means a
    replayed request cannot create duplicate provider work
    (SYSTEM_ARCHITECTURE.md s6).
    """
    existing = session.scalar(
        select(ProcessingJob).where(ProcessingJob.dedupe_key == dedupe_key)
    )
    if existing:
        return existing

    job = ProcessingJob(
        submission_version_id=submission_version_id,
        job_type=job_type,
        state=JobState.queued,
        dedupe_key=dedupe_key,
        max_attempts=settings.max_job_attempts,
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return session.scalar(
            select(ProcessingJob).where(ProcessingJob.dedupe_key == dedupe_key)
        )
    return job


def claim_next(session: Session) -> ProcessingJob | None:
    """Atomically claim one runnable job.

    SKIP LOCKED means concurrent workers never claim the same job, so no
    provider operation runs twice concurrently.
    """
    job = session.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.state.in_([JobState.queued, JobState.failed_retryable]),
            ProcessingJob.attempt_count < ProcessingJob.max_attempts,
        )
        .order_by(ProcessingJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.state = JobState.running
    job.attempt_count += 1
    job.started_at = job.started_at or utcnow()
    session.flush()
    return job


def mark_succeeded(
    session: Session, job: ProcessingJob, *, provider_reference: str | None = None
) -> None:
    job.state = JobState.succeeded
    job.finished_at = utcnow()
    job.error_summary = None
    job.error_detail = None
    if provider_reference:
        job.provider_reference = provider_reference
    session.flush()


def mark_failed(session: Session, job: ProcessingJob, exc: Exception) -> None:
    """Record a failure honestly, including whether it is terminal.

    A retryable error that has exhausted its attempt budget becomes terminal.
    Nothing here softens or reinterprets the provider's message.
    """
    if isinstance(exc, ProviderError):
        summary, detail, retryable = exc.summary, exc.detail, exc.retryable
    else:
        summary = f"Unexpected {type(exc).__name__}: {exc}"
        detail = traceback.format_exc()
        retryable = False

    attempts_left = job.attempt_count < job.max_attempts
    job.state = (
        JobState.failed_retryable
        if (retryable and attempts_left)
        else JobState.failed_terminal
    )
    job.error_summary = summary
    job.error_detail = detail
    job.finished_at = utcnow()
    session.flush()
    logger.error(
        "job %s (%s) -> %s attempt %d/%d: %s",
        job.id,
        job.job_type,
        job.state,
        job.attempt_count,
        job.max_attempts,
        summary,
    )


def jobs_for_version(
    session: Session, submission_version_id: str
) -> list[ProcessingJob]:
    return list(
        session.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.submission_version_id == submission_version_id)
            .order_by(ProcessingJob.created_at)
        )
    )
