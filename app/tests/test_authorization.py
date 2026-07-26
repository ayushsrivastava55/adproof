"""Authentication, cross-tenant isolation, and the media proxy.

The cross-tenant tests are the point: they assert that a user in workspace A
cannot reach anything in workspace B, including by guessing ids.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

TEST_DB = os.getenv("ADPROOF_TEST_DATABASE_URL", "postgresql+psycopg:///adproof_test")

PASSWORD_A = "correct-horse-battery-a"
PASSWORD_B = "correct-horse-battery-b"


@pytest.fixture(scope="module")
def env():
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
        ws_a, ws_b = Workspace(name="Tenant A"), Workspace(name="Tenant B")
        session.add_all([ws_a, ws_b])
        session.flush()
        user_a = User(
            email="a@example.com",
            display_name="A",
            password_hash=hash_password(PASSWORD_A),
        )
        user_b = User(
            email="b@example.com",
            display_name="B",
            password_hash=hash_password(PASSWORD_B),
        )
        analyst = User(
            email="analyst@example.com",
            display_name="Analyst",
            password_hash=hash_password(PASSWORD_A),
        )
        session.add_all([user_a, user_b, analyst])
        session.flush()
        session.add_all(
            [
                Membership(
                    user_id=user_a.id, workspace_id=ws_a.id,
                    role=Role.workspace_admin,
                ),
                Membership(
                    user_id=user_b.id, workspace_id=ws_b.id,
                    role=Role.workspace_admin,
                ),
                Membership(
                    user_id=analyst.id, workspace_id=ws_a.id, role=Role.analyst
                ),
            ]
        )
        ids = {"ws_a": ws_a.id, "ws_b": ws_b.id}

    with TestClient(adproof.api.main.app) as client:
        yield client, ids


def login(client, email, password):
    c = TestClient(client.app)
    res = c.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return c


CAMPAIGN = {
    "campaign_name": "Tenant campaign",
    "brief_text": "say the code",
    "rules": [
        {
            "rule_type": "required_spoken_phrase",
            "requirement_text": "Say the code",
            "phrase": "AYUSH20",
            "min_occurrences": 1,
        }
    ],
}


# -- authentication --------------------------------------------------------


def test_every_data_endpoint_requires_authentication(env):
    client, _ = env
    anon = TestClient(client.app)
    for method, path in [
        ("get", "/api/campaigns"),
        ("get", "/api/submissions"),
        ("get", "/api/auth/me"),
        ("get", "/api/submissions/anything/report"),
        ("get", "/api/submissions/anything/audit"),
        ("get", "/api/evidence/anything/playback"),
        ("post", "/api/campaigns"),
        ("post", "/api/submissions"),
    ]:
        res = (
            anon.post(path, json={})
            if method == "post"
            else anon.get(path)
        )
        assert res.status_code == 401, f"{method} {path} returned {res.status_code}"


def test_login_rejects_wrong_password(env):
    client, _ = env
    res = TestClient(client.app).post(
        "/api/auth/login", json={"email": "a@example.com", "password": "wrong"}
    )
    assert res.status_code == 401


def test_login_does_not_reveal_whether_an_email_exists(env):
    client, _ = env
    unknown = TestClient(client.app).post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "x"}
    )
    known = TestClient(client.app).post(
        "/api/auth/login", json={"email": "a@example.com", "password": "wrong"}
    )
    assert unknown.status_code == known.status_code == 401
    assert unknown.json()["detail"] == known.json()["detail"]


def test_session_cookie_is_httponly_and_samesite(env):
    client, _ = env
    c = TestClient(client.app)
    res = c.post(
        "/api/auth/login", json={"email": "a@example.com", "password": PASSWORD_A}
    )
    cookie = res.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_password_hash_is_never_returned(env):
    client, _ = env
    c = login(client, "a@example.com", PASSWORD_A)
    for path in ("/api/auth/me",):
        body = c.get(path).text.lower()
        assert "password" not in body
        assert "scrypt" not in body


# -- cross-tenant isolation ------------------------------------------------


def test_user_cannot_read_another_tenants_campaign_or_submission(env):
    client, ids = env
    a = login(client, "a@example.com", PASSWORD_A)
    b = login(client, "b@example.com", PASSWORD_B)

    campaign_id = a.post(
        "/api/campaigns", json={**CAMPAIGN, "workspace_id": ids["ws_a"]}
    ).json()["campaign_id"]
    submission_id = a.post(
        "/api/submissions",
        json={
            "campaign_id": campaign_id,
            "creator_reference": "creator",
            "idempotency_key": "iso-1",
            "source_url": "https://example.invalid/v.mp4",
        },
    ).json()["submission_id"]

    # B knows the ids but has no membership: everything must be 404.
    assert b.get(f"/api/submissions/{submission_id}/report").status_code == 404
    assert b.get(f"/api/submissions/{submission_id}/audit").status_code == 404
    assert b.post(f"/api/submissions/{submission_id}/retry").status_code == 404
    assert (
        b.post(
            "/api/submissions",
            json={
                "campaign_id": campaign_id,
                "creator_reference": "x",
                "idempotency_key": "iso-2",
                "source_url": "https://example.invalid/v.mp4",
            },
        ).status_code
        == 404
    )
    # And B's listings must not contain A's records.
    assert all(c["id"] != campaign_id for c in b.get("/api/campaigns").json())
    assert all(s["id"] != submission_id for s in b.get("/api/submissions").json())


def test_cannot_create_a_campaign_in_a_workspace_you_do_not_belong_to(env):
    client, ids = env
    b = login(client, "b@example.com", PASSWORD_B)
    res = b.post("/api/campaigns", json={**CAMPAIGN, "workspace_id": ids["ws_a"]})
    assert res.status_code == 404


def test_role_is_enforced_not_just_membership(env):
    """An analyst belongs to workspace A but may not create campaigns."""
    client, ids = env
    analyst = login(client, "analyst@example.com", PASSWORD_A)
    res = analyst.post(
        "/api/campaigns", json={**CAMPAIGN, "workspace_id": ids["ws_a"]}
    )
    assert res.status_code == 403
    # But reading is permitted.
    assert analyst.get("/api/campaigns").status_code == 200


def test_confirmed_by_is_the_authenticated_user_not_client_supplied(env):
    """An audit trail a caller can forge is not an audit trail."""
    client, ids = env
    a = login(client, "a@example.com", PASSWORD_A)
    payload = {**CAMPAIGN, "workspace_id": ids["ws_a"], "confirmed_by": "ceo@evil.com"}
    result = a.post("/api/campaigns", json=payload).json()

    import adproof.db as db
    from adproof.models import RuleSetVersion

    with db.session_scope() as session:
        rule_set = session.get(RuleSetVersion, result["rule_set_version_id"])
        assert rule_set.confirmed_by == "a@example.com"


# -- media proxy -----------------------------------------------------------


def test_playback_never_returns_a_provider_url(env):
    """The whole point of the proxy: provider URLs must not reach the client."""
    client, ids = env
    a = login(client, "a@example.com", PASSWORD_A)

    import adproof.db as db
    from adproof.models import (
        EvidenceItem,
        MediaAsset,
        RetrievalRun,
        Rule,
        Submission,
        SubmissionVersion,
    )
    from adproof.states import EvidenceOrigin, EvidenceRole, Modality

    campaign_id = a.post(
        "/api/campaigns", json={**CAMPAIGN, "workspace_id": ids["ws_a"]}
    ).json()["campaign_id"]
    submission_id = a.post(
        "/api/submissions",
        json={
            "campaign_id": campaign_id,
            "creator_reference": "creator",
            "idempotency_key": "media-1",
            "source_url": "https://example.invalid/v.mp4",
        },
    ).json()["submission_id"]

    with db.session_scope() as session:
        from sqlalchemy import select as sa_select

        version = session.scalar(
            sa_select(SubmissionVersion).where(
                SubmissionVersion.submission_id == submission_id
            )
        )
        asset = MediaAsset(
            submission_version_id=version.id,
            provider_video_id="m-x",
            provider_collection_id="c-x",
            provider_stream_url="https://play.videodb.io/v1/secret-uuid.m3u8",
        )
        session.add(asset)
        rule = session.scalar(sa_select(Rule))
        session.flush()
        run = RetrievalRun(
            submission_version_id=version.id,
            rule_id=rule.id,
            plan_version="v1",
            query="q",
            search_type="keyword",
            index_type="spoken_word",
            request_params={},
            counts_toward_measurement=True,
            role=EvidenceRole.supporting,
        )
        session.add(run)
        session.flush()
        item = EvidenceItem(
            retrieval_run_id=run.id,
            media_asset_id=asset.id,
            origin=EvidenceOrigin.live_provider,
            role=EvidenceRole.supporting,
            modality=Modality.spoken,
            start_seconds=12.0,
            confidence_band="high",
            provider_snapshot={},
        )
        session.add(item)
        session.flush()
        evidence_id, asset_id = item.id, asset.id

    body = a.get(f"/api/evidence/{evidence_id}/playback")
    assert body.status_code == 200
    data = body.json()
    assert "play.videodb.io" not in body.text, "provider URL leaked to the client"
    assert data["playback_url"].startswith("/media/")
    assert data["seek_to_seconds"] == 12.0

    # Another tenant must not reach this evidence at all.
    b = login(client, "b@example.com", PASSWORD_B)
    assert b.get(f"/api/evidence/{evidence_id}/playback").status_code == 404


def test_media_proxy_rejects_a_forged_upstream_url(env):
    """Without the signature + host allowlist this would be an SSRF relay."""
    client, ids = env
    a = login(client, "a@example.com", PASSWORD_A)

    from adproof.security import issue_media_token, seal_upstream_url

    import adproof.db as db
    from adproof.models import MediaAsset
    from sqlalchemy import select as sa_select

    with db.session_scope() as session:
        asset = session.scalar(sa_select(MediaAsset))
        asset_id = asset.id
        from adproof.models import User

        user_id = session.scalar(
            sa_select(User).where(User.email == "a@example.com")
        ).id

    token = issue_media_token(user_id, asset_id)

    # A raw URL is not a valid reference at all: references are ciphertext.
    res = a.get(
        f"/media/{token}/segment",
        params={"u": "https://169.254.169.254/latest/meta-data/"},
    )
    assert res.status_code == 403

    # Correctly SEALED but not on the allowlist: the host check must still bite,
    # proving the allowlist is an independent control and not just decoration.
    evil = "https://evil.example.com/x.ts"
    res = a.get(f"/media/{token}/segment", params={"u": seal_upstream_url(evil)})
    assert res.status_code == 403
    assert "host is not allowed" in res.json()["detail"].lower()


def test_proxied_manifest_never_exposes_the_provider_url(env):
    """Signed-but-plaintext references still handed the client the real URL."""
    from adproof.security import seal_upstream_url

    sealed = seal_upstream_url("https://play.videodb.io/v1/secret-uuid.m3u8")
    assert "videodb" not in sealed
    assert "secret-uuid" not in sealed


def test_media_proxy_rejects_an_invalid_or_expired_token(env):
    client, _ = env
    a = login(client, "a@example.com", PASSWORD_A)
    assert a.get("/media/not-a-token/master.m3u8").status_code == 403
