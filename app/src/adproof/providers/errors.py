"""Provider error taxonomy.

Every failure mode maps to exactly one of these. There is no error path that
returns a plausible-looking success value, and no error path that substitutes
fixture data.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for all provider failures."""

    #: Whether another bounded attempt could plausibly succeed.
    retryable: bool = False

    def __init__(self, summary: str, detail: str | None = None) -> None:
        super().__init__(summary)
        self.summary = summary
        self.detail = detail or summary


class ProviderNotConfigured(ProviderError):
    """No usable credential. Never degrade to a local simulation."""

    retryable = False


class ProviderUnavailable(ProviderError):
    """Transport/availability failure. Another attempt may succeed."""

    retryable = True


class ProviderTimeout(ProviderError):
    """The SDK's internal polling budget elapsed.

    Important: this does NOT mean the provider-side job failed. videodb 0.5.1
    polls internally up to max_poll_time and then raises; the job may still be
    running server-side. The message must say so rather than implying failure.
    """

    retryable = True


class ProviderRejected(ProviderError):
    """Provider rejected the request (invalid input, unsupported operation)."""

    retryable = False


class ProviderContractViolation(ProviderError):
    """The provider returned something this adapter cannot honestly interpret.

    Raised instead of coercing a partial or unexpected payload into a
    well-formed internal object. Surfacing this is the correct behaviour: a
    silent coercion would manufacture evidence.
    """

    retryable = False
