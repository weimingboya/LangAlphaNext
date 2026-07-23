from __future__ import annotations

from types import SimpleNamespace

from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, ImageContent, TextContent

from langalpha.agent.context import RunContext
from langalpha.config import get_settings
from langalpha.integrations.mcp import MCPGatewayInterceptor


async def test_mcp_gateway_enforces_context_budget_and_redaction(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-secret-value")
    monkeypatch.setenv("MCP_TOOL_ALLOWLIST", '["quote"]')
    monkeypatch.setenv("MCP_MAX_CALLS_PER_RUN", "1")
    get_settings.cache_clear()
    gateway = MCPGatewayInterceptor()
    context = RunContext(
        project_id="project",
        owner_id="owner",
        workspace_id="workspace",
        product_thread_id="thread",
        turn_id="turn",
        product_run_id="run",
    )
    request = MCPToolCallRequest(
        name="quote",
        args={"symbol": "AAPL"},
        server_name="market",
        runtime=SimpleNamespace(context=context),
    )

    async def handler(_: MCPToolCallRequest) -> CallToolResult:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text="credential=sk-proj-test-secret-value",
                ),
                ImageContent(type="image", data="AA==", mimeType="image/png"),
            ],
            structuredContent={
                "records": [
                    {
                        "symbol": "AAPL",
                        "credential": "sk-proj-test-secret-value",
                    }
                ]
            },
        )

    first = await gateway(request, handler)
    assert isinstance(first, CallToolResult)
    assert first.content[0].text == "credential=[REDACTED]"
    assert isinstance(first.content[1], ImageContent)
    assert first.content[1].data == "AA=="
    assert first.structuredContent == {"records": [{"symbol": "AAPL", "credential": "[REDACTED]"}]}

    second = await gateway(request, handler)
    assert second.isError is True
    assert "budget exceeded" in second.content[0].text

    unauthorized = await gateway(
        request.override(name="trade"),
        handler,
    )
    assert unauthorized.isError is True
    assert "not allowed" in unauthorized.content[0].text
