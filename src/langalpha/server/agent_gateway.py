from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client

from langalpha.domain.models import (
    AgentEvent,
    RunStatus,
    RunStrategy,
    RunView,
    ThreadView,
    UsageSummary,
)
from langalpha.security.redaction import redact_text, redact_value
from langalpha.server.public_projection import normalize_todos, public_messages

_REMOTE_STATUS: dict[str, RunStatus] = {
    "pending": "pending",
    "running": "running",
    "success": "success",
    "error": "error",
    "timeout": "error",
    # Agent Server uses "interrupted" for an explicitly cancelled run. A
    # graph HITL pause instead finishes successfully with checkpoint interrupts.
    "interrupted": "cancelled",
}


def as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return, union-attr]
    return {"value": value}


def _datetime(value: object, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return fallback or datetime.now(UTC)


def _stable_id(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def state_interrupts(state: object) -> list[Any]:
    raw = as_dict(state).get("interrupts", [])
    return raw if isinstance(raw, list) else []


def _has_successor(remotes: list[object], run_id: str) -> bool:
    for remote in remotes:
        metadata = as_dict(remote).get("metadata")
        if isinstance(metadata, dict) and metadata.get("parent_run_id") == run_id:
            return True
    return False


def run_view(
    remote: object,
    *,
    thread_id: str,
    has_checkpoint_interrupt: bool = False,
) -> RunView:
    value = as_dict(remote)
    metadata = value.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    run_id = str(value.get("run_id") or value.get("id") or "")
    if not run_id:
        raise ValueError("Agent Server run has no run_id")
    status = _REMOTE_STATUS.get(str(value.get("status")), "error")
    if status == "success" and has_checkpoint_interrupt:
        status = "interrupted"
    created_at = _datetime(value.get("created_at"))
    error = None
    if status == "error":
        error = redact_text(str(value.get("error") or "Agent Server run failed"))
    return RunView(
        id=run_id,
        thread_id=thread_id,
        turn_id=str(metadata.get("turn_id") or run_id),
        parent_run_id=(str(metadata["parent_run_id"]) if metadata.get("parent_run_id") else None),
        status=status,
        error=error,
        created_at=created_at,
        updated_at=_datetime(value.get("updated_at"), created_at),
    )


def thread_view(remote: object) -> ThreadView:
    value = as_dict(remote)
    thread_id = str(value.get("thread_id") or value.get("id") or "")
    if not thread_id:
        raise ValueError("Agent Server thread has no thread_id")
    metadata = value.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    created_at = _datetime(value.get("created_at"))
    return ThreadView(
        id=thread_id,
        title=str(metadata.get("title") or "New research"),
        metadata=redact_value(metadata),
        created_at=created_at,
        updated_at=_datetime(value.get("updated_at"), created_at),
    )


def state_messages(state: object) -> list[dict[str, Any]]:
    values = as_dict(state).get("values")
    values = values if isinstance(values, dict) else {}
    messages = values.get("messages")
    if not isinstance(messages, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(messages):
        message = as_dict(item)
        message_type = str(message.get("type") or message.get("role") or "")
        role = {
            "human": "user",
            "ai": "assistant",
            "tool": "tool",
        }.get(message_type, message_type)
        normalized.append(
            {
                **redact_value(message),
                "id": str(message.get("id") or f"message-{index}"),
                "role": role,
            }
        )
    return normalized


def normalize_messages(state: object) -> list[dict[str, Any]]:
    return public_messages(state_messages(state))


def state_todos(state: object) -> list[dict[str, Any]]:
    values = as_dict(state).get("values")
    values = values if isinstance(values, dict) else {}
    return normalize_todos(values)


def state_widgets(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    widgets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in messages:
        if message.get("role") != "tool" or message.get("name") != "show_widget":
            continue
        content = message.get("content")
        try:
            widget = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            continue
        if not isinstance(widget, dict):
            continue
        widget_id = str(widget.get("id") or _stable_id(widget))
        if widget_id in seen:
            continue
        seen.add(widget_id)
        widgets.append({**redact_value(widget), "id": widget_id})
    return widgets


def _usage(message: dict[str, Any]) -> dict[str, Any] | None:
    usage = message.get("usage_metadata")
    if not isinstance(usage, dict):
        additional = message.get("additional_kwargs")
        usage = additional.get("usage_metadata") if isinstance(additional, dict) else None
    if not isinstance(usage, dict):
        response = message.get("response_metadata")
        usage = response.get("token_usage") if isinstance(response, dict) else None
    return usage if isinstance(usage, dict) else None


def summarize_usage(
    messages: list[dict[str, Any]],
    *,
    input_cost_per_million: float | None = None,
    output_cost_per_million: float | None = None,
) -> UsageSummary:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cached_input_tokens = 0
    web_search_calls = 0
    seen_web_search_calls: set[str] = set()

    def collect_web_search_calls(value: Any) -> None:
        nonlocal web_search_calls
        if isinstance(value, dict):
            if value.get("type") == "web_search_call":
                action = value.get("action")
                if not isinstance(action, dict) or action.get("type") in {None, "search"}:
                    identifier = value.get("id") or value.get("call_id")
                    if identifier:
                        normalized = str(identifier)
                        if normalized not in seen_web_search_calls:
                            seen_web_search_calls.add(normalized)
                            web_search_calls += 1
                    else:
                        web_search_calls += 1
            for nested in value.values():
                collect_web_search_calls(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_web_search_calls(nested)

    for message in messages:
        collect_web_search_calls(message.get("content"))
        usage = _usage(message)
        if usage is None:
            continue
        current_input = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        current_output = usage.get("output_tokens", usage.get("completion_tokens", 0))
        current_total = usage.get("total_tokens")
        input_tokens += current_input if isinstance(current_input, int) else 0
        output_tokens += current_output if isinstance(current_output, int) else 0
        total_tokens += (
            current_total
            if isinstance(current_total, int)
            else (
                (current_input if isinstance(current_input, int) else 0)
                + (current_output if isinstance(current_output, int) else 0)
            )
        )
        details = usage.get("input_token_details")
        cached = details.get("cache_read", 0) if isinstance(details, dict) else 0
        cached_input_tokens += cached if isinstance(cached, int) else 0
    estimated_cost = None
    if input_cost_per_million is not None and output_cost_per_million is not None:
        estimated_cost = round(
            (input_tokens * input_cost_per_million + output_tokens * output_cost_per_million)
            / 1_000_000,
            8,
        )
    return UsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cached_input_tokens,
        web_search_calls=web_search_calls,
        estimated_cost_usd=estimated_cost,
    )


def normalize_stream_part(
    part: object,
    *,
    thread_id: str,
    graph_run_id: str,
) -> AgentEvent:
    raw_name = str(getattr(part, "event", "unknown"))
    payload = redact_value(as_dict(getattr(part, "data", {})))
    if raw_name.startswith("messages/complete"):
        event_type = "message.completed"
    elif raw_name.startswith("messages"):
        event_type = "message.delta"
    elif raw_name.startswith("updates"):
        event_type = "interrupt.requested" if "__interrupt__" in payload else "state.updated"
    elif raw_name.startswith("custom"):
        custom_type = payload.get("type")
        event_type = {
            "asset.ready": "asset.ready",
            "asset.failed": "asset.failed",
            "sandbox.bound": "sandbox.bound",
            "widget.ready": "widget.ready",
        }.get(str(custom_type), "agent.custom")
    elif raw_name.startswith("metadata"):
        event_type = "agent.metadata"
    elif raw_name.startswith("error"):
        event_type = "run.error"
    else:
        event_type = f"agent.{raw_name.replace('/', '.')}"
    part_id = getattr(part, "id", None)
    event_id = str(part_id or f"volatile:{graph_run_id}:{_stable_id([raw_name, payload])}")
    return AgentEvent(
        id=event_id,
        thread_id=thread_id,
        run_id=graph_run_id,
        type=event_type,
        payload=payload,
    )


class AgentGateway:
    """Thin adapter around the official Agent Server SDK resource clients."""

    def __init__(self, server_url: str, *, api_key: str | None = None) -> None:
        self.client = get_client(url=server_url, api_key=api_key)

    async def healthcheck(self, assistant_id: str) -> None:
        await self.client.assistants.get(assistant_id)

    async def create_thread(
        self,
        *,
        metadata: dict[str, Any],
        thread_id: str | None = None,
    ) -> ThreadView:
        remote = await self.client.threads.create(metadata=metadata, thread_id=thread_id)
        return thread_view(remote)

    async def search_threads(
        self,
        *,
        metadata: dict[str, Any],
        limit: int = 100,
        offset: int = 0,
    ) -> list[ThreadView]:
        remotes = await self.client.threads.search(
            metadata=metadata,
            limit=limit,
            offset=offset,
            sort_by="updated_at",
            sort_order="desc",
        )
        return [thread_view(remote) for remote in remotes]

    async def get_thread(self, thread_id: str) -> ThreadView:
        return thread_view(await self.client.threads.get(thread_id))

    async def update_thread_metadata(
        self,
        thread_id: str,
        updates: dict[str, Any],
    ) -> ThreadView:
        current = await self.get_thread(thread_id)
        remote = await self.client.threads.update(
            thread_id,
            metadata={**current.metadata, **updates},
        )
        if remote is None:
            return await self.get_thread(thread_id)
        return thread_view(remote)

    async def delete_thread(self, thread_id: str) -> None:
        await self.client.threads.delete(thread_id)

    async def state(self, thread_id: str) -> dict[str, Any]:
        return as_dict(await self.client.threads.get_state(thread_id))

    async def run(
        self,
        thread_id: str,
        run_id: str,
    ) -> RunView:
        remote = await self.client.runs.get(thread_id, run_id)
        state = await self.state(thread_id)
        remotes = await self.client.runs.list(thread_id, limit=100)
        return run_view(
            remote,
            thread_id=thread_id,
            has_checkpoint_interrupt=(
                bool(state_interrupts(state)) or _has_successor(remotes, run_id)
            ),
        )

    async def runs(
        self,
        thread_id: str,
        *,
        limit: int = 100,
    ) -> list[RunView]:
        remotes = await self.client.runs.list(thread_id, limit=limit)
        state = await self.state(thread_id)
        interrupts = bool(state_interrupts(state))
        result: list[RunView] = []
        for index, remote in enumerate(remotes):
            remote_id = str(as_dict(remote).get("run_id") or as_dict(remote).get("id") or "")
            result.append(
                run_view(
                    remote,
                    thread_id=thread_id,
                    has_checkpoint_interrupt=(
                        (interrupts and index == 0)
                        or (bool(remote_id) and _has_successor(remotes, remote_id))
                    ),
                )
            )
        return result

    async def create(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        strategy: RunStrategy = "enqueue",
        **kwargs: Any,
    ) -> dict[str, Any]:
        return as_dict(
            await self.client.runs.create(
                thread_id,
                assistant_id,
                multitask_strategy=strategy,
                stream_mode=["messages", "updates", "custom"],
                stream_subgraphs=True,
                stream_resumable=True,
                **kwargs,
            )
        )

    async def cancel(self, thread_id: str, run_id: str) -> None:
        await self.client.runs.cancel(
            thread_id,
            run_id,
            wait=True,
            action="interrupt",
        )
