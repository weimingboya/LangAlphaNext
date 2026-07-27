import json
import logging
from datetime import UTC, datetime
from typing import Any

from deepagents.middleware.async_subagents import (
    AsyncSubAgent,
    AsyncSubAgentMiddleware,
    CheckAsyncTaskSchema,
    StartAsyncTaskSchema,
)
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from langgraph_sdk import get_client, get_sync_client

from langalpha.agent.context import RunContext

logger = logging.getLogger(__name__)


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

        def start_async_task(
            description: str,
            subagent_type: str,
            runtime: ToolRuntime,
        ) -> str | Command:
            return self._start_async_task(description, subagent_type, runtime)

        async def astart_async_task(
            description: str,
            subagent_type: str,
            runtime: ToolRuntime,
        ) -> str | Command:
            return await self._astart_async_task(description, subagent_type, runtime)

        compact_start = StructuredTool.from_function(
            func=start_async_task,
            coroutine=astart_async_task,
            name="start_async_task",
            description=next(
                tool.description for tool in self.tools if tool.name == "start_async_task"
            ),
            infer_schema=False,
            args_schema=StartAsyncTaskSchema,
        )
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
            (
                compact_start
                if tool.name == "start_async_task"
                else compact_check
                if tool.name == "check_async_task"
                else tool
            )
            for tool in self.tools
        ]

    @staticmethod
    def _launch_context(
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ) -> tuple[RunContext, dict[str, Any]] | str:
        context = getattr(runtime, "context", None)
        if not isinstance(context, RunContext):
            return "Async research requires a server-issued RunContext."
        metadata = {
            "schema_version": 1,
            "app_id": context.app_id,
            "project_id": context.project_id,
            "owner_id": context.owner_id,
            "parent_thread_id": context.thread_id,
            "parent_turn_id": context.turn_id,
            "thread_kind": "async_subagent",
            "agent_name": subagent_type,
            "title": description[:200],
        }
        return context, metadata

    @staticmethod
    def _child_context(context: RunContext, thread_id: str) -> dict[str, Any]:
        return {
            "app_id": context.app_id,
            "project_id": context.project_id,
            "owner_id": context.owner_id,
            "thread_id": thread_id,
            "turn_id": context.turn_id,
            "input_asset_ids": [],
            "expected_sandbox_id": None,
        }

    @staticmethod
    def _launch_command(
        *,
        context: RunContext,
        description: str,
        subagent_type: str,
        thread_id: str,
        run_id: str,
        tool_call_id: str | None,
    ) -> Command:
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        task = {
            "task_id": thread_id,
            "agent_name": subagent_type,
            "thread_id": thread_id,
            "run_id": run_id,
            "status": "running",
            "description": description,
            "parent_thread_id": context.thread_id,
            "parent_turn_id": context.turn_id,
            "created_at": now,
            "last_checked_at": now,
            "last_updated_at": now,
        }
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"Launched async subagent. task_id: {thread_id}",
                        tool_call_id=tool_call_id,
                    )
                ],
                "async_tasks": {thread_id: task},
            }
        )

    def _start_async_task(
        self,
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ) -> str | Command:
        spec = self._agent_map.get(subagent_type)
        if spec is None:
            return f"Unknown async subagent type: {subagent_type!r}"
        resolved = self._launch_context(description, subagent_type, runtime)
        if isinstance(resolved, str):
            return resolved
        context, metadata = resolved
        if spec.get("url") is None:
            return "In-process async subagents require async invocation."
        client = get_sync_client(
            url=spec.get("url"),
            headers=_resolved_headers(spec),
        )
        thread_id = ""
        try:
            thread = client.threads.create(metadata=metadata)
            thread_id = str(thread["thread_id"])
            run = client.runs.create(
                thread_id=thread_id,
                assistant_id=spec["graph_id"],
                input={"messages": [{"role": "user", "content": description}]},
                metadata={**metadata, "thread_id": thread_id, "turn_id": context.turn_id},
                context=self._child_context(context, thread_id),
            )
        except Exception as exc:
            if thread_id:
                try:
                    client.threads.delete(thread_id)
                except Exception:
                    logger.exception("Failed to clean up async task thread %s", thread_id)
            logger.warning("Failed to launch async subagent %s: %s", subagent_type, exc)
            return f"Failed to launch async subagent '{subagent_type}'."
        return self._launch_command(
            context=context,
            description=description,
            subagent_type=subagent_type,
            thread_id=thread_id,
            run_id=str(run["run_id"]),
            tool_call_id=runtime.tool_call_id,
        )

    async def _astart_async_task(
        self,
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ) -> str | Command:
        spec = self._agent_map.get(subagent_type)
        if spec is None:
            return f"Unknown async subagent type: {subagent_type!r}"
        resolved = self._launch_context(description, subagent_type, runtime)
        if isinstance(resolved, str):
            return resolved
        context, metadata = resolved
        client = get_client(
            url=spec.get("url"),
            headers=_resolved_headers(spec),
        )
        thread_id = ""
        try:
            thread = await client.threads.create(metadata=metadata)
            thread_id = str(thread["thread_id"])
            run = await client.runs.create(
                thread_id=thread_id,
                assistant_id=spec["graph_id"],
                input={"messages": [{"role": "user", "content": description}]},
                metadata={**metadata, "thread_id": thread_id, "turn_id": context.turn_id},
                context=self._child_context(context, thread_id),
            )
        except Exception as exc:
            if thread_id:
                try:
                    await client.threads.delete(thread_id)
                except Exception:
                    logger.exception("Failed to clean up async task thread %s", thread_id)
            logger.warning("Failed to launch async subagent %s: %s", subagent_type, exc)
            return f"Failed to launch async subagent '{subagent_type}'."
        return self._launch_command(
            context=context,
            description=description,
            subagent_type=subagent_type,
            thread_id=thread_id,
            run_id=str(run["run_id"]),
            tool_call_id=runtime.tool_call_id,
        )

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
