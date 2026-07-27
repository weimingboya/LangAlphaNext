from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
)
from mcp.types import CallToolResult, TextContent

from langalpha.agent.context import RunContext
from langalpha.config import get_settings
from langalpha.security.redaction import redact_value


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=message)],
    )


class MCPGatewayInterceptor:
    """Enforce host-side MCP context, allowlist, budget, and redaction."""

    def __init__(self) -> None:
        self._tool_names: frozenset[str] = frozenset()

    def set_tool_names(self, names: set[str]) -> None:
        self._tool_names = frozenset(names)

    def _within_budget(self, runtime: object) -> bool:
        settings = get_settings()
        state = getattr(runtime, "state", None)
        messages = state.get("messages") if isinstance(state, dict) else None
        if not isinstance(messages, list):
            return False
        current_turn: list[Any] = messages
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            message_type = (
                message.get("type") if isinstance(message, dict) else getattr(message, "type", "")
            )
            if message_type in {"human", "user"}:
                current_turn = messages[index + 1 :]
                break
        count = 0
        for message in current_turn:
            tool_calls = (
                message.get("tool_calls")
                if isinstance(message, dict)
                else getattr(message, "tool_calls", None)
            )
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
                if isinstance(name, str) and name in self._tool_names:
                    count += 1
        return count <= settings.mcp_max_calls_per_run

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
    ) -> MCPToolCallResult:
        settings = get_settings()
        qualified_name = f"{request.server_name}_{request.name}"
        if settings.mcp_tool_allowlist and not any(
            name in {request.name, qualified_name} for name in settings.mcp_tool_allowlist
        ):
            return _error_result("MCP capability is not allowed for this profile")

        runtime_context = getattr(request.runtime, "context", None)
        if not isinstance(runtime_context, RunContext):
            return _error_result("MCP capability requires a server-issued RunContext")
        if not self._within_budget(request.runtime):
            return _error_result("MCP call budget exceeded for this run")

        try:
            result = await handler(request)
        except Exception:
            return _error_result("MCP tool failed in the host runtime")

        if isinstance(result, CallToolResult):
            sanitized = redact_value(result.model_dump(mode="json", by_alias=True))
            return CallToolResult.model_validate(sanitized)
        return result


_GATEWAY = MCPGatewayInterceptor()


def _run_coroutine_in_thread(coroutine: Any) -> Any:
    result: list[Any] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as exc:  # pragma: no cover - forwarded to caller
            error.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def _run_sync(coroutine: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    return _run_coroutine_in_thread(coroutine)


@lru_cache(maxsize=1)
def load_mcp_tools() -> tuple[BaseTool, ...]:
    """Load configured MCP tools into the Agent Server host process.

    MCP credentials and connections never enter Daytona. Tools are discovered
    once per server process and keep the upstream schemas exposed to the model.
    """

    settings = get_settings()
    if not settings.mcp_connections:
        return ()
    client = MultiServerMCPClient(
        settings.mcp_connections,  # type: ignore[arg-type]
        tool_interceptors=[_GATEWAY],
        tool_name_prefix=settings.mcp_tool_name_prefix,
        handle_tool_errors=True,
    )
    tools = tuple(_run_sync(client.get_tools()))
    _GATEWAY.set_tool_names({tool.name for tool in tools})
    return tools
