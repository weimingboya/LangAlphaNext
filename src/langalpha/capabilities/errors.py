from __future__ import annotations

from langgraph.types import default_retry_on


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


def is_retryable_model_error(exc: Exception) -> bool:
    """Retry transient model failures, never deterministic request-size failures."""
    message = str(exc).casefold()
    if any(
        marker in message
        for marker in (
            "request too large",
            "context length exceeded",
            "context_length_exceeded",
            "maximum context length",
            "input is too long",
        )
    ):
        return False
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code in {408, 409, 425, 429} or status_code >= 500
    return default_retry_on(exc)
