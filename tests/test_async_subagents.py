from __future__ import annotations

import inspect

from langchain.tools import ToolRuntime

from langalpha.agent.async_subagents import (
    CompactAsyncSubAgentMiddleware,
    compact_async_result,
)
from langalpha.agent.responses import EvidenceItem, ResearchResult


def test_compact_async_result_prefers_structured_response() -> None:
    structured = ResearchResult(
        summary="Apple FY2025 revenue is USD 416.161 billion.",
        evidence=[
            EvidenceItem(
                claim="Revenue was USD 416,161,000,000.",
                source="https://data.sec.gov/example",
                confidence=0.99,
            )
        ],
        limitations=[],
    )
    result = compact_async_result(
        {
            "status": "success",
            "run_id": "run-1",
            "trace_id": "trace-1",
        },
        {
            "structured_response": structured,
            "messages": [
                {
                    "content": [
                        {
                            "type": "reasoning",
                            "encrypted_content": "large-private-payload",
                        },
                        {"type": "text", "text": "Verbose duplicate answer"},
                    ]
                }
            ],
        },
        thread_id="thread-1",
    )

    assert result == {
        "status": "success",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "trace_id": "trace-1",
        "result": structured.model_dump(mode="json"),
    }
    assert "large-private-payload" not in str(result)


def test_compact_async_result_fallback_keeps_only_text() -> None:
    result = compact_async_result(
        {"status": "success", "run_id": "run-2"},
        {
            "messages": [
                {
                    "content": [
                        {"type": "reasoning", "encrypted_content": "omit-me"},
                        {"type": "text", "text": "Final answer"},
                    ]
                }
            ]
        },
        thread_id="thread-2",
    )

    assert result["result"] == "Final answer"
    assert "omit-me" not in str(result)


def test_compact_check_tool_preserves_runtime_injection_annotation() -> None:
    middleware = CompactAsyncSubAgentMiddleware(
        async_subagents=[
            {
                "name": "researcher",
                "description": "Research",
                "graph_id": "researcher",
            }
        ]
    )
    check_tool = next(tool for tool in middleware.tools if tool.name == "check_async_task")

    assert inspect.signature(check_tool.coroutine).parameters["runtime"].annotation is ToolRuntime
