from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from hashlib import sha256
from threading import Lock
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


def _scope_key(request: ModelRequest[Any]) -> str:
    context = getattr(request.runtime, "context", None)
    turn_id = getattr(context, "turn_id", None)
    if turn_id:
        return str(turn_id)
    for message in reversed(request.messages):
        if getattr(message, "type", "") != "human":
            continue
        identifier = getattr(message, "id", None)
        if identifier:
            return str(identifier)
        content = str(getattr(message, "content", ""))
        return sha256(content.encode("utf-8")).hexdigest()
    return f"runtime:{id(request.runtime)}"


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
        self._counts: OrderedDict[str, int] = OrderedDict()
        self._lock = Lock()

    def _before(self, request: ModelRequest[Any]) -> tuple[str, ModelRequest[Any]]:
        key = _scope_key(request)
        with self._lock:
            current = self._counts.get(key, 0)
            if key in self._counts:
                self._counts.move_to_end(key)
        if current >= self.max_calls:
            request = _without_web_search(request)
        return key, request

    def _after(self, key: str, response: ModelResponse[Any]) -> ModelResponse[Any]:
        used = sum(count_web_search_calls(message) for message in response.result)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + used
            self._counts.move_to_end(key)
            while len(self._counts) > 2_000:
                self._counts.popitem(last=False)
        return response

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        key, bounded_request = self._before(request)
        return self._after(key, handler(bounded_request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        key, bounded_request = self._before(request)
        return self._after(key, await handler(bounded_request))
