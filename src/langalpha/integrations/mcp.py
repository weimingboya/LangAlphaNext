from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
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
        self._lock = threading.Lock()
        self._calls: OrderedDict[str, int] = OrderedDict()

    def _consume_budget(self, run_id: str) -> bool:
        settings = get_settings()
        with self._lock:
            count = self._calls.get(run_id, 0) + 1
            self._calls[run_id] = count
            self._calls.move_to_end(run_id)
            while len(self._calls) > 1_000:
                self._calls.popitem(last=False)
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
        if not self._consume_budget(runtime_context.product_run_id):
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
    return tuple(_run_sync(client.get_tools()))
