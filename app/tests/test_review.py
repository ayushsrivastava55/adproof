"""The review workspace, end to end.

Asserts the guarantees that make a review record worth having: overrides need a
reason, the machine result survives untouched, roles are enforced, the gate
cannot be bypassed, and history is append-only.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

TEST_DB = os.getenv("ADPROOF_TEST_DATABASE_URL", "postgresql+psycopg:///adproof_test")
PASSWORD = "review-password-1234"

CAMPAIGN = {
    "campaign_name": "Review campaign",
    "brief_text": "say the code; show the pack",
    "rules": [
        {
            "rule_type": "required_spoken_phrase",
            "requirement_text": "Creator must say AYUSH20",
            "phrase": "AYUSH20",
            "min_occurrences": 1,
            "severity": "blocking",
            "absence_policy": "fail_when_coverage_complete",
        },
        {
            "rule_type": "min_visual_duration",
            "requirement_text": "Pack visible 6s",
            "visual_concept": "pack",
            "min_duration_seconds": 6.0,
            "severity": "required",
        },
    ],
}


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
        ws = Workspace(name="Review WS")
        session.add(ws)
        session.flush()
        people = {}
        for email, role in [
            ("manager@example.com", Role.campaign_manager),
            ("reviewer@example.com", Role.reviewer),
            ("analyst@example.com", Role.analyst),
        ]:
            user = User(
                email=email,
                display_name=email,
                password_hash=hash_password(PASSWORD),
            )
            session.add(user)
            session.flush()
            session.add(
                Membership(user_id=user.id, workspace_id=ws.id, role=role)
            )
            people[email] = user.id

    with TestClient(adproof.api.main.app) as c:
        yield c


def as_user(env, email):
    c = TestClient(env.app)
    assert c.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    ).status_code == 200
    return c


@pytest.fixture
def submission(env):
    """A submission with completed processing and real evaluation results."""
    import adproof.db as db
    from adproof.models import (
        EvaluationResult,
        ProcessingJob,
        Rule,
        SubmissionVersion,
    )
    from adproof.states import (
        AbsenceClass,
        ConfidenceBand,
        JobState,
        JobType,
        RuleResultState,
    )

    manager = as_user(env, "manager@example.com")
    campaign_id = manager.post("/api/campaigns", json=CAMPAIGN).json()["campaign_id"]
    sid = manager.post(
        "/api/submissions",
        json={
            "campaign_id": campaign_id,
            "creator_reference": "creator",
            "idempotency_key": f"rev-{os.urandom(4).hex()}",
            "source_url": "https://example.invalid/v.mp4",
        },
    ).json()["submission_id"]

    with db.session_scope() as session:
        version = session.scalar(
            select(SubmissionVersion).where(SubmissionVersion.submission_id == sid)
        )
        # Mark the pipeline finished so the gate is not blocked on processing.
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.submission_version_id == version.id
            )
        )
        job.state = JobState.succeeded
        rules = list(
            session.scalars(
                select(Rule)
                .where(Rule.rule_set_version_id == version.rule_set_version_id)
                .order_by(Rule.ordinal)
            )
        )
        # Rule 0 (blocking) failed on absence; rule 1 passed.
        session.add(
            EvaluationResult(
                submission_version_id=version.id,
                rule_id=rules[0].id,
                evaluator_version="evaluator/v1",
                state=RuleResultState.failed,
                absence_class=AbsenceClass.likely_absent,
                confidence_band=ConfidenceBand.unavailable,
                explanation="No matching evidence was found.",
            )
        )
        session.add(
            EvaluationResult(
                submission_version_id=version.id,
                rule_id=rules[1].id,
                evaluator_version="evaluator/v1",
                state=RuleResultState.passed,
                absence_class=AbsenceClass.not_applicable,
                confidence_band=ConfidenceBand.high,
                explanation="Measured 9.0s against a threshold of 6s.",
            )
        )
        ids = {"submission": sid, "blocking_rule": rules[0].id, "ok_rule": rules[1].id}
    return ids


# -- gate ------------------------------------------------------------------


def test_absence_based_blocking_failure_is_not_a_rejection_recommendation(
    env, submission
):
    manager = as_user(env, "manager@example.com")
    report = manager.get(f"/api/submissions/{submission['submission']}/report").json()
    adj = report["adjudication"]
    assert adj["machine_recommendation"] == "request_changes"
    assert "approve" not in adj["permitted_decisions"]
    assert submission["blocking_rule"] in adj["blocking_rule_ids"]


def test_cannot_approve_while_a_rule_is_unresolved(env, submission):
    manager = as_user(env, "manager@example.com")
    res = manager.post(
        f"/api/submissions/{submission['submission']}/decision",
        json={"decision": "approve", "rationale": "looks fine to me"},
    )
    assert res.status_code == 409
    assert "not available yet" in res.json()["detail"]


# -- rule review -----------------------------------------------------------


def test_override_requires_a_reason(env, submission):
    reviewer = as_user(env, "reviewer@example.com")
    base = f"/api/submissions/{submission['submission']}/rules/{submission['blocking_rule']}/review"
    for body in [
        {"action": "override", "human_state": "pass"},
        {"action": "override", "human_state": "pass", "reason_category": "false_positive"},
        {
            "action": "override",
            "human_state": "pass",
            "reason_category": "false_positive",
            "reason_text": "   ",
        },
    ]:
        assert reviewer.post(base, json=body).status_code == 422, body


def test_override_records_the_machine_result_immutably(env, submission):
    reviewer = as_user(env, "reviewer@example.com")
    res = reviewer.post(
        f"/api/submissions/{submission['submission']}/rules/{submission['blocking_rule']}/review",
        json={
            "action": "override",
            "human_state": "pass",
            "reason_category": "false_negative",
            "reason_text": "Creator says the code at 0:14; transcription missed it.",
            "evidence_viewed": [],
        },
    )
    assert res.status_code == 201
    assert res.json()["machine_state"] == "fail"
    assert res.json()["human_state"] == "pass"

    report = reviewer.get(
        f"/api/submissions/{submission['submission']}/report"
    ).json()
    rule = next(
        r for r in report["rules"] if r["id"] == submission["blocking_rule"]
    )
    # The machine result is untouched and still visible next to the override.
    assert rule["result"]["state"] == "fail"
    assert rule["result"]["source"] == "machine"
    assert rule["reviews"][-1]["human_state"] == "pass"
    assert rule["reviews"][-1]["reviewer"] == "reviewer@example.com"
    assert rule["reviews"][-1]["reason_category"] == "false_negative"


def test_review_history_is_append_only(env, submission):
    reviewer = as_user(env, "reviewer@example.com")
    url = f"/api/submissions/{submission['submission']}/rules/{submission['blocking_rule']}/review"
    reviewer.post(
        url,
        json={
            "action": "override",
            "human_state": "pass",
            "reason_category": "false_negative",
            "reason_text": "first call",
        },
    )
    reviewer.post(
        url,
        json={
            "action": "override",
            "human_state": "fail",
            "reason_category": "policy_disagreement",
            "reason_text": "changed my mind on reflection",
        },
    )
    report = reviewer.get(
        f"/api/submissions/{submission['submission']}/report"
    ).json()
    rule = next(
        r for r in report["rules"] if r["id"] == submission["blocking_rule"]
    )
    assert len(rule["reviews"]) >= 2, "an earlier review was overwritten"
    # Latest wins for the effective state, but the first is still on record.
    assert rule["reviews"][-1]["human_state"] == "fail"
    assert any(rv["reason_text"] == "first call" for rv in rule["reviews"])


def test_cannot_review_a_rule_with_no_machine_result(env):
    """Nothing to confirm or override before evaluation has run."""
    manager = as_user(env, "manager@example.com")
    campaign_id = manager.post("/api/campaigns", json=CAMPAIGN).json()["campaign_id"]
    sid = manager.post(
        "/api/submissions",
        json={
            "campaign_id": campaign_id,
            "creator_reference": "creator",
            "idempotency_key": f"noresult-{os.urandom(4).hex()}",
            "source_url": "https://example.invalid/v.mp4",
        },
    ).json()["submission_id"]
    report = manager.get(f"/api/submissions/{sid}/report").json()
    rule_id = report["rules"][0]["id"]
    res = manager.post(
        f"/api/submissions/{sid}/rules/{rule_id}/review",
        json={"action": "confirm"},
    )
    assert res.status_code == 409


# -- roles -----------------------------------------------------------------


def test_analyst_cannot_review_or_decide(env, submission):
    analyst = as_user(env, "analyst@example.com")
    assert analyst.post(
        f"/api/submissions/{submission['submission']}/rules/{submission['blocking_rule']}/review",
        json={"action": "confirm"},
    ).status_code == 403
    assert analyst.post(
        f"/api/submissions/{submission['submission']}/decision",
        json={"decision": "request_changes", "rationale": "no"},
    ).status_code == 403


def test_reviewer_may_route_but_not_give_final_approval(env, submission):
    """DATA_MODEL.md assigns final decisions to the campaign manager."""
    reviewer = as_user(env, "reviewer@example.com")
    assert reviewer.post(
        f"/api/submissions/{submission['submission']}/decision",
        json={"decision": "request_changes", "rationale": "code not audible"},
    ).status_code == 201

    assert reviewer.post(
        f"/api/submissions/{submission['submission']}/decision",
        json={"decision": "approve", "rationale": "fine"},
    ).status_code == 403


# -- decisions -------------------------------------------------------------


def test_full_review_flow_reaches_an_approval(env, submission):
    reviewer = as_user(env, "reviewer@example.com")
    manager = as_user(env, "manager@example.com")
    sid = submission["submission"]

    reviewer.post(
        f"/api/submissions/{sid}/rules/{submission['blocking_rule']}/review",
        json={
            "action": "override",
            "human_state": "pass",
            "reason_category": "false_negative",
            "reason_text": "Code is clearly said at 0:14; transcript missed it.",
        },
    )
    report = manager.get(f"/api/submissions/{sid}/report").json()
    assert "approve" in report["adjudication"]["permitted_decisions"]
    # The recommendation re-computes as reviews land: with the blocking rule
    # resolved to pass, approval is now what the policy itself suggests.
    assert report["adjudication"]["machine_recommendation"] == "approve"

    res = manager.post(
        f"/api/submissions/{sid}/decision",
        json={"decision": "approve", "rationale": "Override reviewed and agreed."},
    )
    assert res.status_code == 201
    assert res.json()["submission_state"] == "approved"

    final = manager.get(f"/api/submissions/{sid}/report").json()
    assert final["submission"]["state"] == "approved"
    decision = final["decisions"][-1]
    assert decision["decided_by"] == "manager@example.com"
    assert decision["machine_recommendation"] == "approve"
    assert decision["agreed_with_machine"] is True


def test_disagreement_with_the_machine_is_recorded_as_such(env, submission):
    """Override rate is a quality metric, so agreement must be measurable."""
    manager = as_user(env, "manager@example.com")
    sid = submission["submission"]

    # No rule review: the machine still recommends request_changes. The manager
    # routes it the same way, so this is an agreement...
    report = manager.get(f"/api/submissions/{sid}/report").json()
    assert report["adjudication"]["machine_recommendation"] == "request_changes"

    manager.post(
        f"/api/submissions/{sid}/decision",
        json={"decision": "escalate", "rationale": "compliance should look"},
    )
    final = manager.get(f"/api/submissions/{sid}/report").json()
    decision = final["decisions"][-1]
    # ...but escalating instead is a disagreement, and is recorded as one.
    assert decision["decision"] == "escalate"
    assert decision["machine_recommendation"] == "request_changes"
    assert decision["agreed_with_machine"] is False


def test_decision_requires_a_rationale(env, submission):
    manager = as_user(env, "manager@example.com")
    assert manager.post(
        f"/api/submissions/{submission['submission']}/decision",
        json={"decision": "request_changes", "rationale": ""},
    ).status_code == 422


def test_decisions_are_recorded_in_the_audit_trail(env, submission):
    manager = as_user(env, "manager@example.com")
    sid = submission["submission"]
    manager.post(
        f"/api/submissions/{sid}/decision",
        json={"decision": "escalate", "rationale": "needs compliance review"},
    )
    events = manager.get(f"/api/submissions/{sid}/audit").json()
    categories = [e["category"] for e in events]
    assert "decision.escalate" in categories
    escalation = next(e for e in events if e["category"] == "decision.escalate")
    assert escalation["actor"] == "manager@example.com"
