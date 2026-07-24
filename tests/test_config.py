from __future__ import annotations

from langalpha.config import Settings


def test_empty_optional_secret_and_price_environment_values_are_ignored(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DAYTONA_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    monkeypatch.setenv("OPENAI_INPUT_COST_PER_MILLION", "")
    monkeypatch.setenv("OPENAI_OUTPUT_COST_PER_MILLION", "")

    settings = Settings(_env_file=None)
    assert settings.openai_api_key is None
    assert settings.daytona_api_key is None
    assert settings.langsmith_api_key is None
    assert settings.openai_input_cost_per_million is None
    assert settings.openai_output_cost_per_million is None
