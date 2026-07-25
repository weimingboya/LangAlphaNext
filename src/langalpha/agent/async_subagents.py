import json
from datetime import UTC, datetime
from typing import Any

from deepagents.middleware.async_subagents import (
    AsyncSubAgent,
    AsyncSubAgentMiddleware,
    CheckAsyncTaskSchema,
)
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from langgraph_sdk import get_client, get_sync_client


def _resolved_headers(spec: AsyncSubAgent) -> dict[str, str]:
    headers = dict(spec.get("headers") or {})
    headers.setdefault("x-auth-scheme", "langsmith")
    return headers


def _last_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = "\n".join(
                str(block["text"]).strip()
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and str(block.get("text", "")).strip()
            )
            if text:
                return text
    return "(completed with no output)"


def compact_async_result(
    run: dict[str, Any],
    thread_values: dict[str, Any],
    *,
    thread_id: str,
) -> dict[str, Any]:
    """Return only the completed subagent contract, never its reasoning history."""
    status = str(run["status"])
    run_id = str(run.get("run_id") or run.get("id") or "")
    result: dict[str, Any] = {
        "status": status,
        "thread_id": thread_id,
        "run_id": run_id,
        "trace_id": str(run.get("trace_id") or run_id),
    }
    if status == "success":
        structured = thread_values.get("structured_response")
        if hasattr(structured, "model_dump"):
            structured = structured.model_dump(mode="json")
        result["result"] = (
            structured
            if structured is not None
            else _last_text(thread_values.get("messages") or [])
        )
    elif status == "error":
        error = run.get("error") or "The async subagent encountered an error."
        result["error"] = str(error)[:2_000]
    return result


class CompactAsyncSubAgentMiddleware(AsyncSubAgentMiddleware):
    """Deep Agents async lifecycle with compact structured-result collection."""

    def __init__(self, *, async_subagents: list[AsyncSubAgent]) -> None:
        super().__init__(async_subagents=async_subagents)
        self._agent_map = {agent["name"]: agent for agent in async_subagents}

        def check_async_task(
            task_id: str,
            runtime: ToolRuntime,
        ) -> str | Command:
            return self._check_async_task(task_id, runtime)

        async def acheck_async_task(
            task_id: str,
            runtime: ToolRuntime,
        ) -> str | Command:
            return await self._acheck_async_task(task_id, runtime)

        compact_check = StructuredTool.from_function(
            func=check_async_task,
            coroutine=acheck_async_task,
            name="check_async_task",
            description=(
                "Check one async subagent task. On success, returns only its compact "
                "structured result, status, run ID, and trace ID."
            ),
            infer_schema=False,
            args_schema=CheckAsyncTaskSchema,
        )
        self.tools = [
            compact_check if tool.name == "check_async_task" else tool for tool in self.tools
        ]

    @staticmethod
    def _tracked_task(task_id: str, runtime: ToolRuntime) -> dict[str, Any] | str:
        task = (runtime.state.get("async_tasks") or {}).get(task_id.strip())
        if task is None:
            return f"No tracked task found for task_id: {task_id!r}"
        return task

    @staticmethod
    def _command(
        result: dict[str, Any],
        task: dict[str, Any],
        tool_call_id: str | None,
    ) -> Command:
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        updated_task = {
            **task,
            "status": result["status"],
            "last_checked_at": now,
            "last_updated_at": (
                now if task["status"] != result["status"] else task["last_updated_at"]
            ),
        }
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        json.dumps(
                            result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "async_tasks": {task["task_id"]: updated_task},
            }
        )

    def _check_async_task(
        self,
        task_id: str,
        runtime: ToolRuntime,
    ) -> str | Command:
        task = self._tracked_task(task_id, runtime)
        if isinstance(task, str):
            return task
        spec = self._agent_map[task["agent_name"]]
        if spec.get("url") is None:
            return "In-process async subagents require async invocation."
        client = get_sync_client(
            url=spec["url"],
            headers=_resolved_headers(spec),
        )
        try:
            run = client.runs.get(
                thread_id=task["thread_id"],
                run_id=task["run_id"],
            )
            thread_values: dict[str, Any] = {}
            if run["status"] == "success":
                thread = client.threads.get(thread_id=task["thread_id"])
                thread_values = thread.get("values") or {}
        except Exception as exc:
            return f"Failed to get async task status: {exc}"
        result = compact_async_result(run, thread_values, thread_id=task["thread_id"])
        return self._command(result, task, runtime.tool_call_id)

    async def _acheck_async_task(
        self,
        task_id: str,
        runtime: ToolRuntime,
    ) -> str | Command:
        task = self._tracked_task(task_id, runtime)
        if isinstance(task, str):
            return task
        spec = self._agent_map[task["agent_name"]]
        client = get_client(
            url=spec.get("url"),
            headers=_resolved_headers(spec),
        )
        try:
            run = await client.runs.get(
                thread_id=task["thread_id"],
                run_id=task["run_id"],
            )
            thread_values: dict[str, Any] = {}
            if run["status"] == "success":
                thread = await client.threads.get(thread_id=task["thread_id"])
                thread_values = thread.get("values") or {}
        except Exception as exc:
            return f"Failed to get async task status: {exc}"
        result = compact_async_result(run, thread_values, thread_id=task["thread_id"])
        return self._command(result, task, runtime.tool_call_id)
