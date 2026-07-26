"""Idempotency and immutability, verified against a real Postgres database.

These are not unit tests of application logic: they assert that the database
itself refuses to violate the audit guarantees, so the guarantee survives code
that forgets about it.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from adproof.db import _install_immutability_triggers
from adproof.models import (
    Base,
    Campaign,
    MediaAsset,
    RuleSetVersion,
    Submission,
    SubmissionVersion,
    Workspace,
    utcnow,
)
from adproof.orchestrator.jobs import enqueue
from adproof.states import (
    EvidenceOrigin,
    JobType,
    Modality,
    SubmissionState,
)

TEST_DB = os.getenv("ADPROOF_TEST_DATABASE_URL", "postgresql+psycopg:///adproof_test")


@pytest.fixture(scope="module")
def engine():
    try:
        eng = create_engine(TEST_DB, future=True)
        with eng.connect():
            pass
    except Exception:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable at {TEST_DB}")
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        _install_immutability_triggers(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    s = maker()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def version(session):
    workspace = Workspace(name=f"ws-{utcnow().timestamp()}")
    session.add(workspace)
    session.flush()
    campaign = Campaign(workspace_id=workspace.id, name="c")
    session.add(campaign)
    session.flush()
    rule_set = RuleSetVersion(
        campaign_id=campaign.id,
        version=1,
        confirmed_at=utcnow(),
        confirmed_by="tester",
    )
    session.add(rule_set)
    session.flush()
    submission = Submission(
        workspace_id=workspace.id,
        campaign_id=campaign.id,
        creator_reference="creator",
        state=SubmissionState.draft,
        idempotency_key=f"key-{utcnow().timestamp()}",
    )
    session.add(submission)
    session.flush()
    sv = SubmissionVersion(
        submission_id=submission.id,
        version=1,
        rule_set_version_id=rule_set.id,
        source_type="url",
        source_url="https://example.invalid/v.mp4",
        submitted_by="tester",
    )
    session.add(sv)
    session.commit()
    return sv


@pytest.fixture
def rule(session, version):
    from adproof.models import Rule
    from adproof.states import AbsencePolicy, RuleType

    r = Rule(
        rule_set_version_id=version.rule_set_version_id,
        ordinal=0,
        rule_type=RuleType.required_spoken_phrase,
        modality=Modality.spoken,
        requirement_text="Creator must state the code",
        phrase="AYUSH20",
        min_occurrences=1,
        absence_policy=AbsencePolicy.uncertain,
    )
    session.add(r)
    session.commit()
    return r


# -- idempotency -----------------------------------------------------------


def test_enqueue_is_idempotent_for_the_same_dedupe_key(session, version):
    key = f"{version.id}:ingest"
    first = enqueue(
        session,
        submission_version_id=version.id,
        job_type=JobType.ingest,
        dedupe_key=key,
    )
    session.commit()
    second = enqueue(
        session,
        submission_version_id=version.id,
        job_type=JobType.ingest,
        dedupe_key=key,
    )
    session.commit()
    assert first.id == second.id, "a replayed request created duplicate provider work"


def test_duplicate_submission_idempotency_key_is_rejected(session, version):
    submission = session.get(Submission, version.submission_id)
    clone = Submission(
        workspace_id=submission.workspace_id,
        campaign_id=submission.campaign_id,
        creator_reference="creator",
        state=SubmissionState.draft,
        idempotency_key=submission.idempotency_key,
    )
    session.add(clone)
    with pytest.raises(DBAPIError):
        session.commit()


# -- enum round-trip -------------------------------------------------------


def test_enum_columns_load_back_as_enum_members(session, version, rule):
    """Regression: plain-str round-trip silently broke `is` comparisons.

    When enum columns loaded as `str`, `rule.rule_type is RuleType.x` evaluated
    False, so visual index jobs were never enqueued -- while every stage still
    reported healthy. Any state check in the pipeline could fail this way, so
    the round-trip is asserted directly.
    """
    from adproof.models import Rule, Submission
    from adproof.states import Modality, RuleType, SubmissionState

    session.expire_all()
    loaded = session.get(Rule, rule.id)
    assert loaded.rule_type is RuleType.required_spoken_phrase
    assert loaded.modality is Modality.spoken
    assert loaded.rule_type.value == "required_spoken_phrase"

    submission = session.get(Submission, version.submission_id)
    assert submission.state is SubmissionState.draft


def test_job_state_loads_back_as_enum_member(session, version):
    from adproof.models import ProcessingJob
    from adproof.states import JobState, JobType

    job = enqueue(
        session,
        submission_version_id=version.id,
        job_type=JobType.index_visual,
        dedupe_key=f"{version.id}:enumcheck",
    )
    session.commit()
    session.expire_all()
    loaded = session.get(ProcessingJob, job.id)
    assert loaded.state is JobState.queued
    assert loaded.job_type is JobType.index_visual


# -- immutability ----------------------------------------------------------


def test_submission_version_cannot_be_updated(session, version):
    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE submission_version SET source_url = :u WHERE id = :i"),
            {"u": "https://example.invalid/changed.mp4", "i": version.id},
        )
        session.commit()


def test_submission_version_cannot_be_deleted(session, version):
    with pytest.raises(DBAPIError):
        session.execute(
            text("DELETE FROM submission_version WHERE id = :i"), {"i": version.id}
        )
        session.commit()


def test_evidence_immutable_via_sql(engine, session, version, rule):
    """Insert evidence with raw SQL, then prove the trigger blocks mutation."""
    asset = MediaAsset(
        submission_version_id=version.id,
        provider_video_id="m-test",
        provider_collection_id="c-test",
    )
    session.add(asset)
    session.commit()

    session.execute(
        text(
            "INSERT INTO retrieval_run "
            "(id, submission_version_id, rule_id, plan_version, query, search_type, "
            " index_type, request_params, counts_toward_measurement, role, created_at) "
            "VALUES ('rr-1', :sv, :rid, 'v1', 'q', 'keyword', 'spoken_word', "
            "'{}'::jsonb, true, 'supporting', now())"
        ),
        {"sv": version.id, "rid": rule.id},
    )
    session.execute(
        text(
            "INSERT INTO evidence_item "
            "(id, retrieval_run_id, media_asset_id, origin, role, modality, "
            " start_seconds, confidence_band, provider_snapshot, created_at) "
            "VALUES ('ev-1', 'rr-1', :ma, :origin, 'supporting', 'spoken', "
            "12.5, 'high', '{}'::jsonb, now())"
        ),
        {"ma": asset.id, "origin": EvidenceOrigin.live_provider.value},
    )
    session.commit()

    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE evidence_item SET start_seconds = 99 WHERE id = 'ev-1'")
        )
        session.commit()
    session.rollback()

    with pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM evidence_item WHERE id = 'ev-1'"))
        session.commit()
    session.rollback()


def test_confirmed_rule_set_cannot_be_modified(session, version):
    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE rule_set_version SET confirmed_by = 'x' WHERE id = :i"),
            {"i": version.rule_set_version_id},
        )
        session.commit()


def test_audit_events_are_append_only(session, version):
    submission = session.get(Submission, version.submission_id)
    session.execute(
        text(
            "INSERT INTO audit_event "
            "(id, workspace_id, category, subject_type, subject_id, actor, created_at) "
            "VALUES ('ae-1', :ws, 'submission.created', 'submission', :sid, "
            "'tester', now())"
        ),
        {"ws": submission.workspace_id, "sid": submission.id},
    )
    session.commit()
    with pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM audit_event WHERE id = 'ae-1'"))
        session.commit()


def test_evidence_rejects_reversed_interval(session, version, rule):
    """An interval that ends before it starts is not a real moment."""
    asset = MediaAsset(
        submission_version_id=version.id,
        provider_video_id="m-reversed",
        provider_collection_id="c-test",
    )
    session.add(asset)
    session.flush()
    session.execute(
        text(
            "INSERT INTO retrieval_run "
            "(id, submission_version_id, rule_id, plan_version, query, search_type, "
            " index_type, request_params, counts_toward_measurement, role, created_at) "
            "VALUES ('rr-rev', :sv, :rid, 'v1', 'q', 'keyword', 'spoken_word', "
            "'{}'::jsonb, true, 'supporting', now())"
        ),
        {"sv": version.id, "rid": rule.id},
    )
    session.commit()

    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO evidence_item "
                "(id, retrieval_run_id, media_asset_id, origin, role, modality, "
                " start_seconds, end_seconds, confidence_band, provider_snapshot, "
                " created_at) "
                "VALUES ('ev-bad', 'rr-rev', :ma, :o, 'supporting', 'spoken', "
                "20.0, 5.0, 'high', '{}'::jsonb, now())"
            ),
            {"ma": asset.id, "o": EvidenceOrigin.live_provider.value},
        )
        session.commit()
    session.rollback()


def test_evidence_rejects_negative_start(session, version, rule):
    asset = MediaAsset(
        submission_version_id=version.id,
        provider_video_id="m-neg",
        provider_collection_id="c-test",
    )
    session.add(asset)
    session.flush()
    session.execute(
        text(
            "INSERT INTO retrieval_run "
            "(id, submission_version_id, rule_id, plan_version, query, search_type, "
            " index_type, request_params, counts_toward_measurement, role, created_at) "
            "VALUES ('rr-neg', :sv, :rid, 'v1', 'q', 'keyword', 'spoken_word', "
            "'{}'::jsonb, true, 'supporting', now())"
        ),
        {"sv": version.id, "rid": rule.id},
    )
    session.commit()

    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO evidence_item "
                "(id, retrieval_run_id, media_asset_id, origin, role, modality, "
                " start_seconds, confidence_band, provider_snapshot, created_at) "
                "VALUES ('ev-neg', 'rr-neg', :ma, :o, 'supporting', 'spoken', "
                "-1.0, 'high', '{}'::jsonb, now())"
            ),
            {"ma": asset.id, "o": EvidenceOrigin.live_provider.value},
        )
        session.commit()
    session.rollback()
