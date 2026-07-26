"""Configuration.

The VideoDB key is read server-side only and is never serialised into any API
response or template (SECURITY_AND_PRIVACY.md s4).
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigurationError(RuntimeError):
    """Raised when required configuration is absent.

    Deliberately fatal at the point of use rather than defaulted, so a missing
    key surfaces as an integration error instead of a degraded silent mode.
    """


@dataclass(frozen=True)
class Settings:
    database_url: str
    videodb_api_key: str | None
    videodb_collection_id: str | None
    #: Bounded retry budget for retryable provider failures.
    max_job_attempts: int
    worker_poll_seconds: float
    #: Whether session cookies require HTTPS. False only in development.
    cookies_secure: bool

    def require_videodb_api_key(self) -> str:
        if not self.videodb_api_key:
            raise ConfigurationError(
                "VIDEODB_API_KEY is not configured. AdProof cannot ingest, index, "
                "or search media without a real VideoDB credential, and does not "
                "substitute fixture data."
            )
        return self.videodb_api_key


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv(
            "ADPROOF_DATABASE_URL", "postgresql+psycopg:///adproof"
        ),
        videodb_api_key=os.getenv("VIDEODB_API_KEY") or None,
        videodb_collection_id=os.getenv("VIDEODB_COLLECTION_ID") or None,
        max_job_attempts=int(os.getenv("ADPROOF_MAX_JOB_ATTEMPTS", "3")),
        worker_poll_seconds=float(os.getenv("ADPROOF_WORKER_POLL_SECONDS", "2")),
        cookies_secure=os.getenv("ADPROOF_ENV", "development") != "development",
    )


settings = load_settings()
