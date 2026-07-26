"""Authentication and workspace authorization.

Authorization is checked at the application layer for every resource
(SECURITY_AND_PRIVACY.md s2). Provider asset IDs and stream URLs grant nothing.

The central rule: a caller may only reach a record whose workspace they hold a
membership in. Resolution always goes record -> workspace -> membership, never
the reverse, so an unguessed id cannot be used to widen access.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Campaign, MediaAsset, Membership, Submission, SubmissionVersion, User
from ..security import SESSION_TTL_SECONDS, TokenError, issue_session, read_session
from ..states import Role

SESSION_COOKIE = "adproof_session"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller and their workspace roles."""

    user: User
    #: workspace_id -> role
    roles: dict[str, Role]

    @property
    def workspace_ids(self) -> list[str]:
        return list(self.roles)

    def role_in(self, workspace_id: str) -> Role | None:
        return self.roles.get(workspace_id)


def set_session_cookie(response: Response, user_id: str, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(user_id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,       # not readable by scripts, limits XSS impact
        samesite="lax",      # blocks cross-site form/AJAX replay
        secure=secure,       # HTTPS-only outside development
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_principal(
    adproof_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    session: Session = Depends(get_session),
) -> Principal:
    """Resolve the caller, or reject with 401."""
    if not adproof_session:
        raise HTTPException(401, "Authentication required.")
    try:
        user_id = read_session(adproof_session)
    except TokenError as exc:
        raise HTTPException(401, f"Session is not valid: {exc}") from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Account is not active.")

    memberships = session.scalars(
        select(Membership).where(Membership.user_id == user.id)
    )
    return Principal(user=user, roles={m.workspace_id: m.role for m in memberships})


def _require(principal: Principal, workspace_id: str, allowed: frozenset) -> Role:
    role = principal.role_in(workspace_id)
    if role is None:
        # 404, not 403: confirming a record exists in a workspace the caller
        # cannot see would itself leak information.
        raise HTTPException(404, "Not found.")
    if role not in allowed:
        raise HTTPException(
            403, f"Role '{role.value}' is not permitted to perform this action."
        )
    return role


def authorize_workspace(
    principal: Principal, workspace_id: str, allowed: frozenset
) -> Role:
    return _require(principal, workspace_id, allowed)


def authorized_campaign(
    session: Session, principal: Principal, campaign_id: str, allowed: frozenset
) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found.")
    _require(principal, campaign.workspace_id, allowed)
    return campaign


def authorized_submission(
    session: Session, principal: Principal, submission_id: str, allowed: frozenset
) -> Submission:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(404, "Submission not found.")
    _require(principal, submission.workspace_id, allowed)
    return submission


def authorized_media_asset(
    session: Session, principal: Principal, media_asset_id: str, allowed: frozenset
) -> MediaAsset:
    """Resolve a media asset only through its owning workspace.

    Walks asset -> submission version -> submission -> workspace, so a raw
    asset id cannot be used to reach media in another tenant.
    """
    asset = session.get(MediaAsset, media_asset_id)
    if asset is None:
        raise HTTPException(404, "Media not found.")
    version = session.get(SubmissionVersion, asset.submission_version_id)
    if version is None:
        raise HTTPException(404, "Media not found.")
    submission = session.get(Submission, version.submission_id)
    if submission is None:
        raise HTTPException(404, "Media not found.")
    _require(principal, submission.workspace_id, allowed)
    return asset
