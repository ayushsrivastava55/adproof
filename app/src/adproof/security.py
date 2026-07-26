"""Password hashing, session tokens, and media-access tokens.

Uses only the standard library. No secret is ever logged, returned in an API
response, or embedded in a report (SECURITY_AND_PRIVACY.md s4).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptography.fernet import Fernet

#: scrypt parameters. Deliberately costly; these are interactive-login grade.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32


class TokenError(Exception):
    """A token was absent, malformed, tampered with, or expired."""


def _app_secret() -> bytes:
    """Signing key for sessions and media tokens.

    Fails loudly when unset in production rather than falling back to a
    predictable default, which would make every signature forgeable.
    """
    secret = os.getenv("ADPROOF_SECRET_KEY")
    if secret:
        return secret.encode()
    if os.getenv("ADPROOF_ENV", "development") != "development":
        raise RuntimeError(
            "ADPROOF_SECRET_KEY must be set outside development. Refusing to "
            "sign sessions with a generated key that changes on restart."
        )
    # Development only: ephemeral key, so sessions die with the process.
    global _DEV_SECRET
    if _DEV_SECRET is None:
        _DEV_SECRET = secrets.token_bytes(32)
    return _DEV_SECRET


_DEV_SECRET: bytes | None = None


# -- passwords -------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash: scrypt$<salt_b64>$<key_b64>."""
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_KEY_BYTES,
    )
    return (
        f"scrypt${base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(key).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification. Returns False on any malformed input."""
    try:
        scheme, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=len(expected),
    )
    return hmac.compare_digest(candidate, expected)


# -- signed tokens ---------------------------------------------------------


def _sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(_app_secret(), body, hashlib.sha256).digest()
    return f"{body.decode()}.{base64.urlsafe_b64encode(signature).decode()}"


def _unsign(token: str) -> dict:
    try:
        body_b64, signature_b64 = token.split(".")
        body = body_b64.encode()
        expected = hmac.new(_app_secret(), body, hashlib.sha256).digest()
        provided = base64.urlsafe_b64decode(signature_b64)
    except (ValueError, TypeError) as exc:
        raise TokenError("Malformed token.") from exc

    if not hmac.compare_digest(expected, provided):
        raise TokenError("Token signature is invalid.")

    payload = json.loads(base64.urlsafe_b64decode(body))
    if payload.get("exp", 0) < time.time():
        raise TokenError("Token has expired.")
    return payload


#: Session lifetime. Short enough to bound a leaked cookie's usefulness.
SESSION_TTL_SECONDS = 12 * 3600
#: Media tokens are much shorter-lived: they authorize raw media bytes.
MEDIA_TOKEN_TTL_SECONDS = 900


def issue_session(user_id: str) -> str:
    return _sign({"sub": user_id, "exp": time.time() + SESSION_TTL_SECONDS})


def read_session(token: str) -> str:
    """Return the user id, or raise TokenError."""
    payload = _unsign(token)
    user_id = payload.get("sub")
    if not user_id:
        raise TokenError("Token carries no subject.")
    return user_id


@dataclass(frozen=True)
class MediaGrant:
    user_id: str
    media_asset_id: str


def issue_media_token(user_id: str, media_asset_id: str) -> str:
    """Short-lived authorization to stream ONE media asset.

    Bound to both the user and the asset, so a leaked token cannot be replayed
    against other media, and expires quickly.
    """
    return _sign(
        {
            "sub": user_id,
            "asset": media_asset_id,
            "exp": time.time() + MEDIA_TOKEN_TTL_SECONDS,
        }
    )


def read_media_token(token: str) -> MediaGrant:
    payload = _unsign(token)
    user_id, asset = payload.get("sub"), payload.get("asset")
    if not user_id or not asset:
        raise TokenError("Media token is incomplete.")
    return MediaGrant(user_id=user_id, media_asset_id=asset)


def _fernet() -> "Fernet":
    """Symmetric key for opaque upstream references, derived from the app secret."""
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(
        hashlib.sha256(b"adproof.upstream.v1" + _app_secret()).digest()
    )
    return Fernet(key)


def seal_upstream_url(url: str) -> str:
    """Encrypt a provider URL into an opaque, tamper-proof reference.

    Signing alone was not enough: a signed-but-plaintext URL still HANDS the
    client the provider address, which it can then fetch directly, bypassing
    every check the proxy performs. Encryption means the client holds a token
    that only this server can resolve.

    Fernet is authenticated encryption, so this also subsumes the integrity
    guarantee a bare signature provided.
    """
    return _fernet().encrypt(url.encode()).decode()


def open_upstream_url(sealed: str) -> str:
    """Decrypt an upstream reference. Raises TokenError on any tampering."""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise TokenError("Upstream media reference is not valid.") from exc
