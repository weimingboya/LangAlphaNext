from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from langgraph_sdk import get_client

from langalpha.domain.models import ProductRun, ProductThread, RunStatus
from langalpha.security.redaction import redact_text, redact_value
from langalpha.server.repository import Repository

_TERMINAL = {"success", "error", "interrupted", "cancelled"}
_STATUS_MAP: dict[str, RunStatus] = {
    "pending": "pending",
    "running": "running",
    "success": "success",
    "error": "error",
    "interrupted": "interrupted",
    "cancelled": "cancelled",
}


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return, union-attr]
    return {"value": value}


def _stable_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _normalize_event(event: str, payload: dict[str, Any]) -> str:
    if event.startswith("messages/complete"):
        return "message.completed"
    if event.startswith("messages"):
        return "message.delta"
    if event.startswith("updates"):
        if "__interrupt__" in payload:
            return "interrupt.requested"
        return "agent.state.updated"
    if event.startswith("custom"):
        custom_type = payload.get("type")
        if custom_type == "sandbox.bound":
            return "sandbox.bound"
        if custom_type == "artifact.changed":
            return "artifact.updated"
        if custom_type == "steering.delivered":
            return "steering.delivered"
        if custom_type == "widget.ready":
            return "widget.ready"
        return "agent.custom"
    if event.startswith("metadata"):
        return "agent.metadata"
    if event.startswith("error"):
        return "run.error"
    return f"agent.{event.replace('/', '.')}"


def _message_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {"content": str(value)}


def _usage_dict(message: dict[str, Any]) -> dict[str, Any] | None:
    usage = message.get("usage_metadata")
    if not isinstance(usage, dict):
        additional_kwargs = message.get("additional_kwargs")
        if isinstance(additional_kwargs, dict):
            usage = additional_kwargs.get("usage_metadata")
    if not isinstance(usage, dict):
        response_metadata = message.get("response_metadata")
        if isinstance(response_metadata, dict):
            usage = response_metadata.get("token_usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    total_tokens = usage.get("total_tokens")
    if total_tokens is None and isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens
    if not any(isinstance(value, int) for value in (input_tokens, output_tokens, total_tokens)):
        return None

    details = usage.get("input_token_details")
    cached_tokens = details.get("cache_read") if isinstance(details, dict) else None
    response_metadata = message.get("response_metadata")
    model = response_metadata.get("model_name") if isinstance(response_metadata, dict) else None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached_tokens,
        "model": model,
    }


def _state_has_interrupts(state: object) -> bool:
    interrupts = _as_dict(state).get("interrupts")
    return isinstance(interrupts, list) and bool(interrupts)


def _normalized_interrupts(payload: dict[str, Any]) -> list[Any]:
    interrupts = payload.get("interrupts", payload.get("__interrupt__", []))
    return interrupts if isinstance(interrupts, list) else []


class RunStreamBridge:
    """Project Agent Server streams into stable, replayable product events."""

    def __init__(
        self,
        repository: Repository,
        server_url: str,
        max_run_seconds: int = 1_200,
        max_reconnect_attempts: int = 8,
        reconnect_max_delay: float = 5.0,
        input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        cost_warning_usd: float = 1.0,
    ) -> None:
        self.repository = repository
        self.client = get_client(url=server_url)
        self.max_run_seconds = max_run_seconds
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_max_delay = reconnect_max_delay
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million
        self.cost_warning_usd = cost_warning_usd
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def watch(self, thread: ProductThread, run: ProductRun) -> None:
        if run.graph_run_id is None:
            raise ValueError("runtime run must be attached before streaming")
        if run.id in self._tasks and not self._tasks[run.id].done():
            return
        task = asyncio.create_task(self._consume(thread, run), name=f"run-stream:{run.id}")
        self._tasks[run.id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(run.id, None))

    async def recover(self) -> None:
        for run in await self.repository.list_active_runs():
            thread = await self.repository.get_thread(run.thread_id)
            if thread is not None:
                self.watch(thread, run)

    async def remote_status(self, thread: ProductThread, run: ProductRun) -> ProductRun:
        if run.graph_run_id is None:
            return run
        remote = await self.client.runs.get(thread.graph_thread_id, run.graph_run_id)
        status = _STATUS_MAP.get(str(remote.get("status")), "error")
        if status == "success":
            state = await self.client.threads.get_state(thread.graph_thread_id)
            if _state_has_interrupts(state):
                status = "interrupted"
        current = await self.repository.get_run(run.id)
        if (
            current is not None
            and current.cancel_requested
            and status in {"interrupted", "cancelled"}
        ):
            status = "cancelled"
        error = None
        if status == "error":
            error = redact_text(str(remote.get("error") or run.error or "runtime run failed"))
        await self.repository.update_run(run.id, status, error=error)
        refreshed = await self.repository.get_run(run.id)
        assert refreshed is not None
        return refreshed

    async def _handle_custom(
        self,
        thread: ProductThread,
        run: ProductRun,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if payload.get("type") == "sandbox.bound":
            sandbox_id = payload.get("sandbox_id")
            if isinstance(sandbox_id, str):
                await self.repository.bind_sandbox(thread.id, sandbox_id)
            return payload
        if payload.get("type") == "artifact.changed":
            path = str(payload.get("path", ""))
            if not path.startswith("/workspace/artifacts/"):
                return payload
            artifact = await self.repository.upsert_artifact(
                thread_id=thread.id,
                run_id=run.id,
                name=str(payload.get("name") or Path(path).name),
                sandbox_path=path,
                media_type=str(payload.get("media_type") or "application/octet-stream"),
                size_bytes=int(payload.get("size_bytes") or 0),
                checksum=(str(payload["checksum"]) if payload.get("checksum") else None),
            )
            return artifact.model_dump(mode="json")
        return payload

    async def _project_usage(
        self,
        thread: ProductThread,
        run: ProductRun,
        message: dict[str, Any],
        message_id: str,
    ) -> None:
        usage = _usage_dict(message)
        if usage is None:
            return
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if (
            self.input_cost_per_million is not None
            and self.output_cost_per_million is not None
            and isinstance(input_tokens, int)
            and isinstance(output_tokens, int)
        ):
            usage["estimated_cost_usd"] = round(
                (
                    input_tokens * self.input_cost_per_million
                    + output_tokens * self.output_cost_per_million
                )
                / 1_000_000,
                8,
            )
        else:
            usage["estimated_cost_usd"] = None
        await self.repository.append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="usage.updated",
            payload=usage,
            source_event_key=(f"runtime:{run.graph_run_id}:message:{message_id}:usage"),
        )
        estimated_cost = usage.get("estimated_cost_usd")
        if isinstance(estimated_cost, float):
            run_cost = round(
                await self.repository.run_estimated_cost(run.id),
                8,
            )
        else:
            run_cost = None
        if run_cost is not None and run_cost >= self.cost_warning_usd:
            await self.repository.append_event(
                thread_id=thread.id,
                run_id=run.id,
                event_type="cost.warning",
                payload={
                    "estimated_run_cost_usd": run_cost,
                    "threshold_usd": self.cost_warning_usd,
                    "model": usage.get("model"),
                },
                source_event_key=f"runtime:{run.graph_run_id}:cost-warning",
            )

    async def _project_part(
        self,
        thread: ProductThread,
        run: ProductRun,
        part: object,
    ) -> None:
        event_name = str(getattr(part, "event", "unknown"))
        payload = _as_dict(getattr(part, "data", {}))
        payload = redact_value(payload)
        part_id = getattr(part, "id", None)
        event_type = _normalize_event(event_name, payload)
        custom_source_hash = _stable_hash(payload) if event_name.startswith("custom") else None
        if event_type == "run.error":
            # The terminal projection below is authoritative and stable. Raw
            # runtime error frames can repeat across rejoin boundaries.
            return
        if event_name.startswith("custom"):
            payload = await self._handle_custom(thread, run, payload)
        if event_type == "interrupt.requested":
            payload = {"interrupts": _normalized_interrupts(payload)}

        # Resumable Agent Server streams provide stable IDs. If an older server
        # omits one, only semantically stable custom/final objects are persisted.
        if event_type == "message.completed":
            raw_messages = payload.get("value")
            final_message = (
                raw_messages[-1] if isinstance(raw_messages, list) and raw_messages else payload
            )
            message = _message_dict(final_message)
            message_id = str(message.get("id") or _stable_hash(message))
            source_key = f"runtime:{run.graph_run_id}:message:{message_id}:final"
        elif event_type in {"sandbox.bound", "artifact.updated", "widget.ready"}:
            source_key = (
                f"runtime:{run.graph_run_id}:{event_type}:"
                f"{custom_source_hash or _stable_hash(payload)}"
            )
        elif event_type == "interrupt.requested":
            source_key = (
                f"runtime:{run.graph_run_id}:interrupt:{_stable_hash(payload['interrupts'])}"
            )
        elif part_id:
            source_key = f"runtime:{run.graph_run_id}:{part_id}"
        else:
            return

        await self.repository.append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type=event_type,
            payload=payload,
            source_event_key=source_key,
        )
        if event_type == "message.completed":
            await self._project_usage(thread, run, message, message_id)
        if part_id:
            await self.repository.set_run_cursor(run.id, str(part_id))

    async def _reconcile(self, thread: ProductThread, run: ProductRun) -> bool:
        state = await self.client.threads.get_state(thread.graph_thread_id)
        values = _as_dict(state).get("values", {})
        if not isinstance(values, dict):
            return _state_has_interrupts(state)

        messages = values.get("messages") or []
        if messages:
            message = _message_dict(messages[-1])
            role = message.get("type") or message.get("role")
            if role in {"ai", "assistant"}:
                message_id = str(message.get("id") or _stable_hash(message))
                await self.repository.append_event(
                    thread_id=thread.id,
                    run_id=run.id,
                    event_type="message.completed",
                    payload=message,
                    source_event_key=(f"runtime:{run.graph_run_id}:message:{message_id}:final"),
                )
                await self._project_usage(thread, run, message, message_id)

        todos = values.get("todos")
        if todos is not None:
            await self.repository.append_event(
                thread_id=thread.id,
                run_id=run.id,
                event_type="todo.updated",
                payload={"todos": todos},
                source_event_key=f"runtime:{run.graph_run_id}:todos:{_stable_hash(todos)}",
            )

        interrupts = _as_dict(state).get("interrupts")
        if interrupts:
            normalized = _normalized_interrupts({"interrupts": interrupts})
            await self.repository.append_event(
                thread_id=thread.id,
                run_id=run.id,
                event_type="interrupt.requested",
                payload={"interrupts": normalized},
                source_event_key=(
                    f"runtime:{run.graph_run_id}:interrupt:{_stable_hash(normalized)}"
                ),
            )
        return _state_has_interrupts(state)

    async def _consume(self, thread: ProductThread, run: ProductRun) -> None:
        assert run.graph_run_id is not None
        await self.repository.update_run(run.id, "running")
        await self.repository.append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="run.started",
            payload={"run_id": run.id},
            source_event_key=f"runtime:{run.graph_run_id}:started",
        )
        try:
            async with asyncio.timeout(self.max_run_seconds):
                remote: dict[str, Any] | None = None
                reconnect_attempt = 0
                while True:
                    cursor = await self.repository.get_run_cursor(run.id)
                    attempt_incremented = False
                    try:
                        async for part in self.client.runs.join_stream(
                            thread.graph_thread_id,
                            run.graph_run_id,
                            cancel_on_disconnect=False,
                            last_event_id=cursor,
                        ):
                            await self._project_part(thread, run, part)
                        remote = await self.client.runs.get(
                            thread.graph_thread_id, run.graph_run_id
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        reconnect_attempt += 1
                        attempt_incremented = True
                        if reconnect_attempt > self.max_reconnect_attempts:
                            raise
                        try:
                            remote = await self.client.runs.get(
                                thread.graph_thread_id, run.graph_run_id
                            )
                        except Exception:
                            remote = None

                    remote_status = str(remote.get("status")) if remote is not None else "running"
                    if remote_status in _TERMINAL:
                        break
                    if not attempt_incremented:
                        reconnect_attempt += 1
                    if reconnect_attempt > self.max_reconnect_attempts:
                        raise RuntimeError("Agent Server stream rejoin budget exhausted")
                    await asyncio.sleep(
                        min(
                            0.25 * (2 ** (reconnect_attempt - 1)),
                            self.reconnect_max_delay,
                        )
                    )

                assert remote is not None
                interrupted = await self._reconcile(thread, run)
            remote_status = str(remote.get("status", "success"))
            mapped = "interrupted" if interrupted else _STATUS_MAP.get(remote_status, "error")
            current = await self.repository.get_run(run.id)
            if (
                current is not None
                and current.cancel_requested
                and mapped in {"interrupted", "cancelled"}
            ):
                mapped = "cancelled"
            error = None
            if mapped == "error":
                error = redact_text(str(remote.get("error") or "runtime run failed"))
            await self.repository.update_run(run.id, mapped, error=error)
            terminal_payload: dict[str, Any] = {"run_id": run.id}
            if error is not None:
                terminal_payload["error"] = error
            await self.repository.append_event(
                thread_id=thread.id,
                run_id=run.id,
                event_type=f"run.{mapped}",
                payload=terminal_payload,
                source_event_key=f"runtime:{run.graph_run_id}:terminal:{mapped}",
            )
            for guidance in await self.repository.reclaim_guidance(run.id):
                await self.repository.append_event(
                    thread_id=thread.id,
                    run_id=run.id,
                    event_type="steering.reclaimed",
                    payload=guidance.model_dump(mode="json"),
                    source_event_key=f"guidance:{guidance.id}:reclaimed",
                )
        except TimeoutError:
            await self.client.runs.cancel(
                thread.graph_thread_id,
                run.graph_run_id,
                wait=False,
            )
            error = f"run exceeded {self.max_run_seconds} seconds"
            await self.repository.update_run(run.id, "error", error=error)
            await self.repository.append_event(
                thread_id=thread.id,
                run_id=run.id,
                event_type="run.timeout",
                payload={
                    "run_id": run.id,
                    "max_run_seconds": self.max_run_seconds,
                },
                source_event_key=f"runtime:{run.graph_run_id}:timeout",
            )
            await self.repository.reclaim_guidance(run.id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = redact_text(type(exc).__name__)
            await self.repository.update_run(run.id, "error", error=error)
            await self.repository.append_event(
                thread_id=thread.id,
                run_id=run.id,
                event_type="run.error",
                payload={"run_id": run.id, "error": error},
                source_event_key=f"runtime:{run.graph_run_id}:bridge-error",
            )
            await self.repository.reclaim_guidance(run.id)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
