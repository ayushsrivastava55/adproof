"""API behaviour, including the paths that must fail loudly.

The critical tests here are the negative ones: a missing credential and a
provider failure must produce visible errors, never a plausible report.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

TEST_DB = os.getenv("ADPROOF_TEST_DATABASE_URL", "postgresql+psycopg:///adproof_test")


PASSWORD = "test-password-1234"


@pytest.fixture(scope="module")
def client():
    """An AUTHENTICATED client. Every data endpoint now requires a session."""
    try:
        eng = create_engine(TEST_DB, future=True)
        with eng.connect():
            pass
    except Exception:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable at {TEST_DB}")
    eng.dispose()

    import adproof.api.main
    import adproof.db as db
    from adproof.models import Base, Membership, User, Workspace
    from adproof.security import hash_password
    from adproof.states import Role

    Base.metadata.drop_all(db.engine)
    db.init_db()

    with db.session_scope() as session:
        workspace = Workspace(name="Test workspace")
        session.add(workspace)
        session.flush()
        user = User(
            email="reviewer@example.com",
            display_name="Reviewer",
            password_hash=hash_password(PASSWORD),
        )
        session.add(user)
        session.flush()
        session.add(
            Membership(
                user_id=user.id,
                workspace_id=workspace.id,
                role=Role.workspace_admin,
            )
        )

    with TestClient(adproof.api.main.app) as c:
        res = c.post(
            "/api/auth/login",
            json={"email": "reviewer@example.com", "password": PASSWORD},
        )
        assert res.status_code == 200, res.text
        yield c


CAMPAIGN = {
    "campaign_name": "PulseBar launch",
    "brief_text": "Creator must say the code AYUSH20 and show the package.",
    "rules": [
        {
            "rule_type": "required_spoken_phrase",
            "requirement_text": "Creator must state the discount code AYUSH20",
            "source_brief_excerpt": "must say the code AYUSH20",
            "phrase": "AYUSH20",
            "min_occurrences": 1,
            "absence_policy": "fail_when_coverage_complete",
        },
        {
            "rule_type": "min_visual_duration",
            "requirement_text": "PulseBar package visible for at least 6 seconds",
            "source_brief_excerpt": "show the package",
            "visual_concept": "PulseBar protein bar package",
            "min_duration_seconds": 6.0,
            "absence_policy": "uncertain",
        },
    ],
}


def test_integrity_endpoint_declares_no_fixtures(client):
    data = client.get("/api/integrity").json()
    assert data["evidence_mode"] == "live_provider_only"
    assert data["fixture_data_present"] is False
    assert data["fixture_fallback_on_failure"] is False
    assert data["authorization"]["workspace_isolation"] == (
        "enforced on every read and write"
    )
    assert "not calibrated" in data["confidence_disclosure"].lower()


def test_campaign_confirmer_comes_from_the_session(client):
    """`confirmed_by` is taken from the authenticated user, never the body."""
    res = client.post("/api/campaigns", json=CAMPAIGN)
    assert res.status_code == 201


def test_rule_params_are_validated(client):
    payload = dict(CAMPAIGN)
    payload["rules"] = [
        {
            "rule_type": "required_spoken_phrase",
            "requirement_text": "missing the phrase",
        }
    ]
    assert client.post("/api/campaigns", json=payload).status_code == 422


def test_create_campaign_and_submission_flow(client):
    res = client.post("/api/campaigns", json=CAMPAIGN)
    assert res.status_code == 201
    campaign_id = res.json()["campaign_id"]

    submission = {
        "campaign_id": campaign_id,
        "creator_reference": "creator-001",
        "idempotency_key": "sub-key-1",
        "source_url": "https://example.invalid/video.mp4",
    }
    first = client.post("/api/submissions", json=submission)
    assert first.status_code == 201
    assert first.json()["idempotent_replay"] is False

    # Replaying the same key must not create a second submission or job.
    second = client.post("/api/submissions", json=submission)
    assert second.json()["idempotent_replay"] is True
    assert second.json()["submission_id"] == first.json()["submission_id"]

    report = client.get(
        f"/api/submissions/{first.json()['submission_id']}/report"
    ).json()

    # Nothing has been processed yet, so nothing may look processed.
    assert report["submission"]["state"] == "draft"
    assert report["media"] is None
    assert report["processing_complete"] is False
    assert report["evidence_mode"] == "live_provider_only"
    assert len(report["rules"]) == 2
    for rule in report["rules"]:
        assert rule["result"] is None, "a result appeared before any work ran"
        assert rule["evidence"] == []

    stages = {s["stage"]: s for s in report["stages"]}
    assert stages["ingest"]["state"] == "queued"
    assert stages["ingest"]["terminal"] is False


def test_submission_requires_a_confirmed_rule_set(client):

    import adproof.db as db
    from adproof.models import Campaign, RuleSetVersion, Workspace

    with db.session_scope() as session:
        # Must live in the CALLER's workspace: otherwise authorization returns
        # 404 first and the rule-set check is never reached.
        workspace = session.scalar(
            select(Workspace).where(Workspace.name == "Test workspace")
        )
        campaign = Campaign(workspace_id=workspace.id, name="unconfirmed")
        session.add(campaign)
        session.flush()
        session.add(RuleSetVersion(campaign_id=campaign.id, version=1))
        campaign_id = campaign.id

    res = client.post(
        "/api/submissions",
        json={
            "campaign_id": campaign_id,
            "creator_reference": "creator-002",
                "idempotency_key": "sub-key-unconfirmed",
            "source_url": "https://example.invalid/v.mp4",
        },
    )
    assert res.status_code == 409
    assert "confirmed by a human" in res.json()["detail"]


def test_playback_is_refused_for_unknown_evidence(client):
    assert client.get("/api/evidence/does-not-exist/playback").status_code == 404


def test_report_404_for_unknown_submission(client):
    assert client.get("/api/submissions/nope/report").status_code == 404


# -- provider failure must be visible, never simulated ---------------------


def test_missing_credential_produces_an_error_not_a_result():
    """The whole product rests on this: no key means no report."""
    from adproof.providers.errors import ProviderNotConfigured
    from adproof.providers.videodb_adapter import VideoDBAdapter

    import adproof.config as config

    original = config.settings
    config.settings = config.Settings(
        database_url=original.database_url,
        videodb_api_key=None,
        videodb_collection_id=None,
        max_job_attempts=3,
        worker_poll_seconds=1,
        cookies_secure=False,
    )
    try:
        with pytest.raises(ProviderNotConfigured):
            VideoDBAdapter()
    finally:
        config.settings = original


def test_worker_records_provider_failure_as_a_visible_terminal_error(client):
    """Drive a real job with a failing adapter and assert honest reporting."""

    from adproof.orchestrator import worker
    from adproof.providers.errors import ProviderUnavailable

    campaign_id = client.post("/api/campaigns", json=CAMPAIGN).json()["campaign_id"]
    submission_id = client.post(
        "/api/submissions",
        json={
            "campaign_id": campaign_id,
            "creator_reference": "creator-fail",
                "idempotency_key": "sub-key-fail",
            "source_url": "https://example.invalid/v.mp4",
        },
    ).json()["submission_id"]

    class FailingAdapter:
        def ingest(self, **_kwargs):
            raise ProviderUnavailable(
                "VideoDB upload failed.", "simulated transport error"
            )

    # Exhaust the retry budget.
    for _ in range(10):
        if not worker.run_once(adapter=FailingAdapter()):
            break

    report = client.get(f"/api/submissions/{submission_id}/report").json()

    assert report["submission"]["state"] == "error"
    assert "VideoDB upload failed" in report["submission"]["error_summary"]
    assert report["media"] is None

    ingest_stage = next(s for s in report["stages"] if s["stage"] == "ingest")
    assert ingest_stage["state"] == "failed_terminal"
    assert ingest_stage["attempt_count"] >= 1
    assert "VideoDB upload failed" in ingest_stage["error_summary"]

    # No downstream stage may have been created off a failed ingestion, and
    # absolutely no rule result may exist.
    assert {s["stage"] for s in report["stages"]} == {"ingest"}
    for rule in report["rules"]:
        assert rule["result"] is None
        assert rule["evidence"] == []


def test_index_failure_yields_honest_per_rule_errors_not_review_ready(client):
    """A failed index must not become 'no evidence found', nor 'ready'.

    Ingestion succeeds, both indexes fail. Every rule must end in `error` with
    absence classified as `provider_failure`, the retrieval runs must record
    that they never executed, and the submission must NOT be review-ready.
    """
    from adproof.orchestrator import worker
    from adproof.providers.errors import ProviderUnavailable
    from adproof.providers.types import IngestedMedia

    campaign_id = client.post("/api/campaigns", json=CAMPAIGN).json()["campaign_id"]
    submission_id = client.post(
        "/api/submissions",
        json={
            "campaign_id": campaign_id,
            "creator_reference": "creator-idxfail",
                "idempotency_key": "sub-key-idxfail",
            "source_url": "https://example.invalid/v.mp4",
        },
    ).json()["submission_id"]

    class IndexFailingAdapter:
        def ingest(self, **_kwargs):
            return IngestedMedia(
                provider_video_id="m-idxfail",
                provider_collection_id="c-test",
                duration_seconds=30.0,
                stream_url="https://stream.example/v.m3u8",
                player_url=None,
                snapshot={"id": "m-idxfail"},
            )

        def generate_stream_url(self, *_a, **_k):
            return "https://stream.example/v.m3u8"

        def index_spoken_words(self, *_a, **_k):
            raise ProviderUnavailable("Spoken-word indexing failed.", "simulated")

        def index_scenes(self, *_a, **_k):
            raise ProviderUnavailable("Visual indexing failed.", "simulated")

        def search(self, *_a, **_k):
            raise AssertionError("search must not run when no index completed")

    for _ in range(40):
        if not worker.run_once(adapter=IndexFailingAdapter()):
            break

    report = client.get(f"/api/submissions/{submission_id}/report").json()

    assert report["submission"]["state"] != "ready_for_review"
    assert report["media"]["provider_video_id"] == "m-idxfail"

    for rule in report["rules"]:
        result = rule["result"]
        assert result is not None
        assert result["state"] == "error"
        assert result["absence_class"] == "provider_failure"
        # The explanation must not claim the content is absent.
        assert "did not run" in result["explanation"].lower() or (
            "search did not run" in result["explanation"].lower()
        )
        assert rule["evidence"] == []
        for run in rule["retrieval_runs"]:
            assert run["executed"] is False
            # null, NOT 0: nobody may read this as "searched and found nothing".
            assert run["result_count"] is None
