from __future__ import annotations

from langalpha.config import Settings


def test_default_model_is_luna_with_medium_reasoning(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)

    settings = Settings(_env_file=None)

    assert settings.openai_model == "gpt-5.6-luna"
    assert settings.openai_reasoning_effort == "medium"
    assert settings.max_researcher_model_calls == 16
    assert settings.max_researcher_tool_calls == 40


def test_empty_optional_secret_and_price_environment_values_are_ignored(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DAYTONA_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    monkeypatch.setenv("LANGGRAPH_API_KEY", "")
    monkeypatch.setenv("FRED_API_KEY", "")
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    monkeypatch.setenv("OPENAI_INPUT_COST_PER_MILLION", "")
    monkeypatch.setenv("OPENAI_OUTPUT_COST_PER_MILLION", "")

    settings = Settings(_env_file=None)
    assert settings.openai_api_key is None
    assert settings.daytona_api_key is None
    assert settings.langsmith_api_key is None
    assert settings.langgraph_api_key is None
    assert settings.fred_api_key is None
    assert settings.massive_api_key is None
    assert settings.massive_snapshots_enabled is False
    assert settings.openai_input_cost_per_million is None
    assert settings.openai_output_cost_per_million is None
