"""Authorized media proxy.

Closes the gap recorded as assumption A-10 and VERIFIED on 2026-07-26: VideoDB
playback URLs return 200 with no credential, so anyone who ever sees one can
watch unpublished creator media forever.

Nothing here trusts the client. Two independent controls guard every fetch:

  1. a short-lived, signed media token bound to BOTH the caller and one asset;
  2. an HMAC over the upstream URL, plus a strict host allowlist.

Without (2) the proxy would fetch arbitrary client-supplied URLs, turning it
into a server-side request forgery relay against anything the server can reach.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import MediaAsset
from ..security import (
    TokenError,
    open_upstream_url,
    read_media_token,
    seal_upstream_url,
)
from ..states import CAN_READ
from .auth import Principal, authorized_media_asset, current_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])

#: The ONLY hosts this proxy will fetch from. Verified against the live service:
#: manifests and segments are served from play.videodb.io.
_ALLOWED_HOSTS = frozenset({"play.videodb.io"})

#: Manifests are small; segments stream. Bounded so a hostile upstream cannot
#: exhaust memory.
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_SEGMENT_CHUNK = 64 * 1024


def _check_host(url: str) -> str:
    """Allowlist check, applied to EVERY upstream fetch without exception."""
    host = (urlparse(url).hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        logger.warning("blocked proxy attempt to non-allowlisted host %r", host)
        raise HTTPException(403, "Upstream host is not allowed.")
    if urlparse(url).scheme != "https":
        raise HTTPException(403, "Upstream must be HTTPS.")
    return url


def _open_upstream(sealed: str) -> str:
    """Decrypt a client-held upstream reference, then allowlist its host.

    The reference is ciphertext, so the client never learns the provider URL
    and cannot bypass this proxy. Decryption is authenticated, so tampering
    fails closed. The host allowlist remains as an independent second control.
    """
    try:
        url = open_upstream_url(sealed)
    except TokenError as exc:
        raise HTTPException(403, str(exc)) from exc
    return _check_host(url)


def _grant(token: str, session: Session) -> str:
    """Validate a media token and return the media_asset_id it authorizes."""
    try:
        grant = read_media_token(token)
    except TokenError as exc:
        raise HTTPException(403, f"Media access token is not valid: {exc}") from exc
    asset = session.get(MediaAsset, grant.media_asset_id)
    if asset is None:
        raise HTTPException(404, "Media not found.")
    return grant.media_asset_id


def _proxied(request: Request, token: str, kind: str, url: str) -> str:
    """Build a URL pointing back at this proxy for a rewritten upstream URL."""
    base = str(request.base_url).rstrip("/")
    query = urlencode({"u": seal_upstream_url(url)})
    return f"{base}/media/{token}/{kind}?{query}"


def _rewrite_manifest(
    body: str, request: Request, token: str, next_kind: str
) -> str:
    """Rewrite absolute URLs in an HLS manifest to point back at this proxy.

    Comment lines (#EXT...) and blank lines pass through untouched; only URI
    lines are rewritten.
    """
    out = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        if stripped.startswith("https://"):
            out.append(_proxied(request, token, next_kind, stripped))
        else:
            # Relative URI. Not observed from this provider; refuse rather than
            # guess at a base and silently fetch the wrong thing.
            raise HTTPException(
                502, "Upstream manifest used an unexpected relative URI form."
            )
    return "\n".join(out) + "\n"


@router.get("/{token}/master.m3u8")
def master_playlist(
    token: str, request: Request, session: Session = Depends(get_session)
) -> Response:
    asset_id = _grant(token, session)
    asset = session.get(MediaAsset, asset_id)
    if not asset or not asset.provider_stream_url:
        raise HTTPException(409, "No playable stream is recorded for this media.")
    # Sourced from our own database, so only the host allowlist applies.
    master_url = _check_host(asset.provider_stream_url)

    try:
        upstream = httpx.get(master_url, timeout=20.0)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not fetch the media manifest: {exc}") from exc

    if len(upstream.content) > _MAX_MANIFEST_BYTES:
        raise HTTPException(502, "Upstream manifest is implausibly large.")

    body = _rewrite_manifest(upstream.text, request, token, "variant.m3u8")
    return Response(content=body, media_type="application/vnd.apple.mpegurl")


@router.get("/{token}/variant.m3u8")
def variant_playlist(
    token: str,
    request: Request,
    u: str = Query(...),
    session: Session = Depends(get_session),
) -> Response:
    _grant(token, session)
    url = _open_upstream(u)
    try:
        upstream = httpx.get(url, timeout=20.0)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not fetch the media playlist: {exc}") from exc

    if len(upstream.content) > _MAX_MANIFEST_BYTES:
        raise HTTPException(502, "Upstream playlist is implausibly large.")

    body = _rewrite_manifest(upstream.text, request, token, "segment")
    return Response(content=body, media_type="application/vnd.apple.mpegurl")


@router.get("/{token}/segment")
def segment(
    token: str,
    u: str = Query(...),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """Stream one media segment. Bytes are relayed, never buffered whole."""
    _grant(token, session)
    url = _open_upstream(u)

    def stream():
        try:
            with httpx.stream("GET", url, timeout=30.0) as upstream:
                upstream.raise_for_status()
                yield from upstream.iter_bytes(_SEGMENT_CHUNK)
        except httpx.HTTPError as exc:
            logger.error("segment fetch failed: %s", exc)
            # The generator has already started; the connection simply ends.
            return

    return StreamingResponse(stream(), media_type="video/mp2t")


# -- token issuance --------------------------------------------------------


def issue_playback(
    session: Session, principal: Principal, media_asset_id: str
) -> dict[str, str]:
    """Authorize the caller for one asset and return proxied playback details.

    The provider URL is deliberately NOT returned: handing it to the client
    would reinstate exactly the leak this module exists to close.
    """
    from ..security import issue_media_token

    authorized_media_asset(session, principal, media_asset_id, CAN_READ)
    token = issue_media_token(principal.user.id, media_asset_id)
    return {
        "playback_url": f"/media/{token}/master.m3u8",
        "access_control": "adproof_proxied_short_lived_token",
    }


__all__ = ["router", "issue_playback", "current_principal"]
