from __future__ import annotations

import re
from typing import Any

from langalpha.config import get_settings

_SECRET_PATTERN = re.compile(r"(?i)(?:sk-(?:proj-)?|dtn_|lsv2_|langsmith_)[A-Za-z0-9_.-]{8,}")


def redact_text(value: str) -> str:
    redacted = _SECRET_PATTERN.sub("[REDACTED]", value)
    settings = get_settings()
    for secret in (
        settings.openai_api_key,
        settings.daytona_api_key,
        settings.langsmith_api_key,
        settings.supabase_secret_key,
    ):
        if secret is not None:
            raw = secret.get_secret_value()
            if raw:
                redacted = redacted.replace(raw, "[REDACTED]")
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value
