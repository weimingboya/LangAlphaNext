from __future__ import annotations


class NonRetryableToolError(RuntimeError):
    """A permanent tool failure that another identical attempt cannot fix."""


def raise_for_provider_status(provider: str, status_code: int) -> None:
    """Raise a retry-aware error for an unsuccessful provider response."""
    if status_code < 400:
        return
    message = f"{provider} request failed with HTTP {status_code}"
    if status_code in {408, 425, 429} or status_code >= 500:
        raise RuntimeError(message)
    raise NonRetryableToolError(message)


def is_retryable_tool_error(exc: Exception) -> bool:
    """Retry transient failures, but not permanent responses or bad arguments."""
    return not isinstance(exc, (NonRetryableToolError, TypeError, ValueError))
