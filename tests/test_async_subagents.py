from __future__ import annotations

import inspect
from types import SimpleNamespace

from langchain.tools import ToolRuntime

import langalpha.agent.async_subagents as module
from langalpha.agent.async_subagents import (
    CompactAsyncSubAgentMiddleware,
    compact_async_result,
)
from langalpha.agent.context import RunContext
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
    start_tool = next(tool for tool in middleware.tools if tool.name == "start_async_task")

    assert inspect.signature(check_tool.coroutine).parameters["runtime"].annotation is ToolRuntime
    assert inspect.signature(start_tool.coroutine).parameters["runtime"].annotation is ToolRuntime


async def test_async_task_inherits_parent_ownership_and_context(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Threads:
        async def create(self, *, metadata):
            calls.append(("thread", metadata))
            return {"thread_id": "child-thread"}

        async def delete(self, thread_id):
            calls.append(("delete", thread_id))

    class Runs:
        async def create(self, **kwargs):
            calls.append(("run", kwargs))
            return {"run_id": "child-run"}

    monkeypatch.setattr(
        module,
        "get_client",
        lambda **_: SimpleNamespace(threads=Threads(), runs=Runs()),
    )
    middleware = CompactAsyncSubAgentMiddleware(
        async_subagents=[
            {
                "name": "researcher",
                "description": "Research",
                "graph_id": "researcher",
            }
        ]
    )
    runtime = SimpleNamespace(
        context=RunContext(
            project_id="project",
            owner_id="owner",
            thread_id="parent-thread",
            turn_id="turn-1",
        ),
        tool_call_id="call-1",
    )

    command = await middleware._astart_async_task(
        "Check Apple revenue",
        "researcher",
        runtime,
    )

    assert calls[0] == (
        "thread",
        {
            "schema_version": 1,
            "project_id": "project",
            "owner_id": "owner",
            "parent_thread_id": "parent-thread",
            "parent_turn_id": "turn-1",
            "thread_kind": "async_subagent",
            "agent_name": "researcher",
            "title": "Check Apple revenue",
        },
    )
    run_kwargs = calls[1][1]
    assert isinstance(run_kwargs, dict)
    assert run_kwargs["metadata"]["parent_thread_id"] == "parent-thread"
    assert run_kwargs["context"] == {
        "project_id": "project",
        "owner_id": "owner",
        "thread_id": "child-thread",
        "turn_id": "turn-1",
        "input_asset_ids": [],
        "expected_sandbox_id": None,
    }
    assert command.update["async_tasks"]["child-thread"]["parent_thread_id"] == "parent-thread"
