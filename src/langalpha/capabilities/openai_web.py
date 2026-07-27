from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse

from langalpha.config import get_settings


def build_openai_web_search_tool() -> dict[str, Any]:
    """Build the provider-hosted OpenAI web search tool declaration."""
    settings = get_settings()
    return {
        "type": "web_search",
        "search_context_size": settings.openai_web_search_context_size,
    }


def _walk_blocks(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_blocks(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_blocks(nested)


def count_web_search_calls(value: Any) -> int:
    """Count unique billable search actions in Responses API content."""
    seen: set[str] = set()
    anonymous = 0
    content = getattr(value, "content", value)
    for block in _walk_blocks(content):
        if block.get("type") != "web_search_call":
            continue
        action = block.get("action")
        if isinstance(action, dict) and action.get("type") not in {None, "search"}:
            continue
        identifier = block.get("id") or block.get("call_id")
        if identifier:
            seen.add(str(identifier))
        else:
            anonymous += 1
    return len(seen) + anonymous


def _current_turn_messages(messages: list[Any]) -> list[Any]:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if getattr(message, "type", "") == "human":
            return messages[index + 1 :]
    return messages


def _without_web_search(request: ModelRequest[Any]) -> ModelRequest[Any]:
    tools = [
        tool
        for tool in request.tools
        if not (isinstance(tool, dict) and tool.get("type") == "web_search")
    ]
    return request.override(tools=tools)


class OpenAIWebSearchBudgetMiddleware(AgentMiddleware):
    """Stops exposing web search after a per-turn provider-call budget is spent."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls

    def _bounded(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        used = sum(
            count_web_search_calls(message) for message in _current_turn_messages(request.messages)
        )
        return _without_web_search(request) if used >= self.max_calls else request

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._bounded(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._bounded(request))
