from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from langgraph_sdk import get_client

_URL = re.compile(r"https?://[^\s<>()\]}\",\\\uFF1B]+")
_TERMINAL_RUN_STATUSES = {"success", "error", "timeout", "interrupted"}


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return, union-attr]
    return {"value": value}


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", "")).strip()
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and str(block.get("text", "")).strip()
    )


def _openai_message(message: object) -> dict[str, Any] | None:
    value = _as_dict(message)
    message_type = str(value.get("type") or value.get("role") or "")
    role = {"human": "user", "ai": "assistant"}.get(message_type, message_type)
    if role not in {"user", "assistant", "tool", "system"}:
        return None
    normalized: dict[str, Any] = {
        "role": role,
        "content": _text_content(value.get("content")),
    }
    if value.get("name"):
        normalized["name"] = str(value["name"])
    tool_calls = value.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        normalized["tool_calls"] = [
            {
                "id": str(call.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(call.get("name") or ""),
                    "arguments": json.dumps(
                        call.get("args") or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                },
            }
            for call in tool_calls
            if isinstance(call, dict)
        ]
    if role == "tool":
        normalized["tool_call_id"] = str(value.get("tool_call_id") or "")
        normalized["content"] = normalized["content"][:20_000]
    return normalized


def normalize_trajectory(messages: list[Any]) -> list[dict[str, Any]]:
    """Return observable messages only; never expose reasoning blocks."""
    return [
        normalized for message in messages if (normalized := _openai_message(message)) is not None
    ]


def _tool_calls(messages: list[Any], *, actor: str, turn: int | None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in messages:
        value = _as_dict(message)
        for call in value.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            calls.append(
                {
                    "actor": actor,
                    "turn": turn,
                    "name": str(call.get("name") or ""),
                    "args": call.get("args") or {},
                    "id": str(call.get("id") or ""),
                }
            )
    return calls


def _tool_results(messages: list[Any], *, actor: str, turn: int | None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for message in messages:
        value = _as_dict(message)
        message_type = str(value.get("type") or value.get("role") or "")
        if message_type != "tool":
            continue
        content = _text_content(value.get("content"))
        try:
            parsed: Any = json.loads(content)
        except json.JSONDecodeError:
            parsed = content
        results.append(
            {
                "actor": actor,
                "turn": turn,
                "name": str(value.get("name") or ""),
                "tool_call_id": str(value.get("tool_call_id") or ""),
                "status": str(value.get("status") or "success"),
                "result": parsed,
            }
        )
    return results


def _usage(messages: list[Any]) -> dict[str, int]:
    input_tokens = output_tokens = total_tokens = 0
    for message in messages:
        value = _as_dict(message)
        additional = value.get("additional_kwargs")
        additional = additional if isinstance(additional, dict) else {}
        usage = additional.get("usage_metadata") or value.get("usage_metadata")
        if not isinstance(usage, dict):
            continue
        current_input = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        current_output = usage.get("output_tokens", usage.get("completion_tokens", 0))
        current_total = usage.get("total_tokens")
        input_tokens += current_input if isinstance(current_input, int) else 0
        output_tokens += current_output if isinstance(current_output, int) else 0
        total_tokens += (
            current_total
            if isinstance(current_total, int)
            else (current_input if isinstance(current_input, int) else 0)
            + (current_output if isinstance(current_output, int) else 0)
        )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _interrupts(state: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in state.get("interrupts") or []:
        value = _as_dict(item)
        interrupt_value = value.get("value")
        if isinstance(interrupt_value, dict):
            normalized.append(interrupt_value)
        else:
            normalized.append(value)
    return normalized


def _task_map(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = state.get("values")
    values = values if isinstance(values, dict) else {}
    tasks = values.get("async_tasks")
    if not isinstance(tasks, dict):
        return {}
    return {
        str(task_id): _as_dict(task)
        for task_id, task in tasks.items()
        if isinstance(task, dict) or hasattr(task, "model_dump")
    }


def _messages(state: dict[str, Any]) -> list[Any]:
    values = state.get("values")
    values = values if isinstance(values, dict) else {}
    messages = values.get("messages")
    return messages if isinstance(messages, list) else []


def _structured_response(state: dict[str, Any]) -> Any:
    values = state.get("values")
    values = values if isinstance(values, dict) else {}
    result = values.get("structured_response")
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


def _last_answer(messages: list[Any]) -> str:
    for message in reversed(messages):
        value = _as_dict(message)
        if str(value.get("type") or value.get("role") or "") not in {"ai", "assistant"}:
            continue
        text = _text_content(value.get("content"))
        if text:
            return text
    return ""


@dataclass(frozen=True, slots=True)
class EvalRunnerConfig:
    server_url: str
    api_key: str | None = None
    poll_interval_seconds: float = 0.5
    timeout_seconds: float = 180.0
    keep_threads: bool = False

    @classmethod
    def from_env(cls) -> EvalRunnerConfig:
        return cls(
            server_url=os.getenv(
                "EVAL_AGENT_SERVER_URL",
                os.getenv("LANGGRAPH_SERVER_URL", "http://127.0.0.1:2024"),
            ),
            api_key=os.getenv("LANGGRAPH_API_KEY") or None,
            timeout_seconds=float(os.getenv("EVAL_RUN_TIMEOUT_SECONDS", "180")),
            keep_threads=os.getenv("EVAL_KEEP_THREADS", "").casefold() == "true",
        )


class AgentServerEvalTarget:
    """Official LangSmith target function backed by real Agent Server runs."""

    def __init__(
        self,
        config: EvalRunnerConfig | None = None,
        *,
        client_factory: Callable[..., Any] = get_client,
    ) -> None:
        self.config = config or EvalRunnerConfig.from_env()
        self._client = client_factory(
            url=self.config.server_url,
            api_key=self.config.api_key,
        )

    async def _wait_for_child(self, task: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.timeout_seconds
        while True:
            run = _as_dict(
                await self._client.runs.get(
                    str(task["thread_id"]),
                    str(task["run_id"]),
                )
            )
            if str(run.get("status")) in _TERMINAL_RUN_STATUSES:
                return run
            if time.monotonic() >= deadline:
                return {**run, "status": "timeout", "error": "eval runner timed out"}
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _run_turn(
        self,
        *,
        thread_id: str,
        graph: str,
        message: str,
        case_id: str,
        turn_index: int,
    ) -> tuple[dict[str, Any], dict[str, Any], float]:
        started = time.monotonic()
        turn_id = str(uuid4())
        kwargs: dict[str, Any] = {
            "input": {"messages": [{"role": "user", "content": message}]},
            "metadata": {
                "purpose": "langalpha-harness-eval",
                "case_id": case_id,
                "turn_index": turn_index,
                "turn_id": turn_id,
            },
            "stream_mode": ["messages", "updates", "custom"],
            "stream_subgraphs": True,
            "stream_resumable": True,
        }
        kwargs["context"] = {
            "project_id": "langalpha-evals",
            "owner_id": "langalpha-eval-runner",
            "thread_id": thread_id,
            "turn_id": turn_id,
            "input_asset_ids": [],
        }
        created = _as_dict(await self._client.runs.create(thread_id, graph, **kwargs))
        run_id = str(created["run_id"])
        await self._client.runs.join(thread_id, run_id)
        remote, state = await asyncio.gather(
            self._client.runs.get(thread_id, run_id),
            self._client.threads.get_state(thread_id, subgraphs=True),
        )
        return _as_dict(remote), _as_dict(state), time.monotonic() - started

    async def run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        case_id = str(inputs["case_id"])
        graph = str(inputs.get("graph") or "main")
        turns = inputs.get("turns")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"{case_id}: turns must be a non-empty list")

        thread_id = str(uuid4())
        child_threads: set[str] = set()
        turn_records: list[dict[str, Any]] = []
        child_records: list[dict[str, Any]] = []
        all_main_messages: list[Any] = []
        final_state: dict[str, Any] = {}
        error: str | None = None
        await self._client.threads.create(
            thread_id=thread_id,
            metadata={"purpose": "langalpha-harness-eval", "case_id": case_id},
        )
        try:
            previous_message_count = 0
            for turn_index, turn in enumerate(turns):
                if not isinstance(turn, dict) or not isinstance(turn.get("message"), str):
                    raise ValueError(f"{case_id}: invalid turn {turn_index}")
                tasks_before = _task_map(final_state)
                if turn.get("wait_for_async"):
                    if not tasks_before:
                        raise RuntimeError(
                            f"{case_id}: turn {turn_index} requested async wait without a task"
                        )
                    await asyncio.gather(
                        *(self._wait_for_child(task) for task in tasks_before.values())
                    )
                task_ids = list(tasks_before)
                message = turn["message"].format(task_id=task_ids[0] if len(task_ids) == 1 else "")
                remote, final_state, turn_latency = await self._run_turn(
                    thread_id=thread_id,
                    graph=graph,
                    message=message,
                    case_id=case_id,
                    turn_index=turn_index,
                )
                interrupts = _interrupts(final_state)
                remote_status = str(remote.get("status") or "unknown")
                effective_status = (
                    "interrupted"
                    if interrupts and remote_status in {"success", "interrupted"}
                    else remote_status
                )
                all_main_messages = _messages(final_state)
                new_messages = all_main_messages[previous_message_count:]
                previous_message_count = len(all_main_messages)
                tasks_after = _task_map(final_state)
                child_threads.update(
                    str(task["thread_id"]) for task in tasks_after.values() if task.get("thread_id")
                )
                turn_records.append(
                    {
                        "index": turn_index,
                        "run_id": str(remote.get("run_id") or ""),
                        "trace_id": str(remote.get("trace_id") or remote.get("run_id") or ""),
                        "status": effective_status,
                        "latency_seconds": round(turn_latency, 3),
                        "answer": _last_answer(new_messages),
                        "interrupts": interrupts,
                        "tool_calls": _tool_calls(
                            new_messages,
                            actor=graph,
                            turn=turn_index,
                        ),
                        "tool_results": _tool_results(
                            new_messages,
                            actor=graph,
                            turn=turn_index,
                        ),
                        "trajectory": normalize_trajectory(new_messages),
                        "tasks": list(tasks_after.values()),
                    }
                )
                if effective_status not in {"success", "interrupted"}:
                    error = (
                        f"Agent Server run {remote.get('run_id')} finished with "
                        f"status {effective_status}: "
                        f"{remote.get('error') or 'no error detail returned'}"
                    )
                    break
                if interrupts:
                    break

            final_tasks = _task_map(final_state)
            for task_id, task in final_tasks.items():
                child_run = await self._wait_for_child(task)
                child_thread_id = str(task["thread_id"])
                child_state = _as_dict(
                    await self._client.threads.get_state(
                        child_thread_id,
                        subgraphs=True,
                    )
                )
                child_messages = _messages(child_state)
                child_records.append(
                    {
                        "task_id": task_id,
                        "thread_id": child_thread_id,
                        "run_id": str(task.get("run_id") or ""),
                        "trace_id": str(child_run.get("trace_id") or task.get("run_id") or ""),
                        "status": str(child_run.get("status") or "unknown"),
                        "error": child_run.get("error"),
                        "structured_response": _structured_response(child_state),
                        "tool_calls": _tool_calls(
                            child_messages,
                            actor="researcher",
                            turn=None,
                        ),
                        "tool_results": _tool_results(
                            child_messages,
                            actor="researcher",
                            turn=None,
                        ),
                        "trajectory": normalize_trajectory(child_messages),
                        "usage": _usage(child_messages),
                    }
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if not self.config.keep_threads:
                for child_thread_id in child_threads:
                    try:
                        await self._client.threads.delete(child_thread_id)
                    except Exception:
                        pass
                try:
                    await self._client.threads.delete(thread_id)
                except Exception:
                    pass

        root_tool_calls = [call for turn in turn_records for call in turn["tool_calls"]]
        root_tool_results = [result for turn in turn_records for result in turn["tool_results"]]
        child_tool_calls = [call for child in child_records for call in child["tool_calls"]]
        child_tool_results = [result for child in child_records for result in child["tool_results"]]
        main_tool_calls = root_tool_calls if graph == "main" else []
        main_tool_results = root_tool_results if graph == "main" else []
        researcher_tool_calls = [
            *(root_tool_calls if graph == "researcher" else []),
            *child_tool_calls,
        ]
        researcher_tool_results = [
            *(root_tool_results if graph == "researcher" else []),
            *child_tool_results,
        ]
        combined_trajectory: list[dict[str, Any]] = []
        for index, turn in enumerate(turn_records):
            combined_trajectory.extend(turn["trajectory"])
            if index == 0 and len(turn_records) > 1:
                for child in child_records:
                    combined_trajectory.append(
                        {
                            "role": "system",
                            "content": (
                                "Observable researcher trajectory for async task "
                                f"{child['task_id']} begins."
                            ),
                        }
                    )
                    combined_trajectory.extend(child["trajectory"])
                    combined_trajectory.append(
                        {
                            "role": "system",
                            "content": (
                                "Observable researcher trajectory ends with status "
                                f"{child['status']}."
                            ),
                        }
                    )

        direct_structured = _structured_response(final_state)
        structured_response = (
            direct_structured
            if direct_structured is not None
            else (child_records[0]["structured_response"] if len(child_records) == 1 else None)
        )
        answer = turn_records[-1]["answer"] if turn_records else ""
        searchable = json.dumps(
            {
                "answer": answer,
                "structured_response": structured_response,
                "tool_results": [*main_tool_results, *researcher_tool_results],
            },
            ensure_ascii=False,
            default=str,
        )
        root_usage = _usage(all_main_messages)
        child_usage = {
            key: sum(child["usage"][key] for child in child_records)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        main_usage = (
            root_usage
            if graph == "main"
            else {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        )
        researcher_usage = {
            key: child_usage[key] + (root_usage[key] if graph == "researcher" else 0)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        return {
            "case_id": case_id,
            "answer": answer,
            "structured_response": structured_response,
            "turns": turn_records,
            "tasks": child_records,
            "main_tool_calls": main_tool_calls,
            "researcher_tool_calls": researcher_tool_calls,
            "tool_results": [*main_tool_results, *researcher_tool_results],
            "trajectory": combined_trajectory,
            "sources": sorted(set(_URL.findall(searchable))),
            "usage": {
                "main": main_usage,
                "researcher": researcher_usage,
                "total_tokens": (main_usage["total_tokens"] + researcher_usage["total_tokens"]),
                "main_tool_calls": len(main_tool_calls),
                "researcher_tool_calls": len(researcher_tool_calls),
                "first_turn_latency_seconds": (
                    turn_records[0]["latency_seconds"] if turn_records else None
                ),
                "latency_seconds": round(time.monotonic() - started, 3),
            },
            "error": error,
        }

    async def __call__(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return await self.run(inputs)
