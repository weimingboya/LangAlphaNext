from __future__ import annotations

from langalpha.config import get_settings
from langalpha.security.redaction import redact_value


def test_recursive_redaction_uses_configured_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-sensitive-value")
    get_settings.cache_clear()
    payload = {
        "error": "bad key sk-proj-test-sensitive-value",
        "nested": ["dtn_123456789abcdef", {"value": "safe"}],
    }
    redacted = redact_value(payload)

    assert redacted == {
        "error": "bad key [REDACTED]",
        "nested": ["[REDACTED]", {"value": "safe"}],
    }
