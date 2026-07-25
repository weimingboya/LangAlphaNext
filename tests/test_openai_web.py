from __future__ import annotations

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from langalpha.capabilities.openai_web import (
    OpenAIWebSearchBudgetMiddleware,
    build_openai_web_search_tool,
    count_web_search_calls,
)
from langalpha.config import get_settings
from langalpha.server.agent_gateway import summarize_usage


def test_openai_web_tool_uses_configured_context_size(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_WEB_SEARCH_CONTEXT_SIZE", "high")
    get_settings.cache_clear()
    assert build_openai_web_search_tool() == {
        "type": "web_search",
        "search_context_size": "high",
    }


def test_web_search_counter_counts_unique_search_actions_only() -> None:
    message = AIMessage(
        content=[
            {"type": "web_search_call", "id": "search-1", "action": {"type": "search"}},
            {"type": "web_search_call", "id": "search-1", "action": {"type": "search"}},
            {"type": "web_search_call", "id": "open-1", "action": {"type": "open_page"}},
            {"type": "web_search_call", "id": "search-2", "action": {"type": "search"}},
        ]
    )
    assert count_web_search_calls(message) == 2


def test_snapshot_usage_counts_searches_separately_from_tokens() -> None:
    usage = summarize_usage(
        [
            {
                "type": "ai",
                "content": [
                    {
                        "type": "web_search_call",
                        "id": "search-1",
                        "action": {"type": "search"},
                    },
                    {
                        "type": "text",
                        "text": "Sourced answer",
                        "annotations": [],
                    },
                ],
                "usage_metadata": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            }
        ]
    )
    assert usage.total_tokens == 120
    assert usage.web_search_calls == 1


def test_web_search_budget_removes_provider_tool_after_limit() -> None:
    middleware = OpenAIWebSearchBudgetMiddleware(max_calls=1)
    request = ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[HumanMessage(content="research", id="turn-message")],
        tools=[{"type": "web_search"}, {"type": "custom"}],
    )

    first = middleware.wrap_model_call(
        request,
        lambda bounded: ModelResponse(
            result=[
                AIMessage(
                    content=[
                        {
                            "type": "web_search_call",
                            "id": "search-1",
                            "action": {"type": "search"},
                        }
                    ]
                )
            ]
        ),
    )
    assert len(first.result) == 1

    captured: list[dict] = []

    def handler(bounded: ModelRequest) -> ModelResponse:
        captured.extend(tool for tool in bounded.tools if isinstance(tool, dict))
        return ModelResponse(result=[AIMessage(content="done")])

    middleware.wrap_model_call(request, handler)
    assert captured == [{"type": "custom"}]
