from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import posixpath
import re
from typing import Any, Literal

from langchain.tools import ToolRuntime, tool
from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from langalpha.agent.context import RunContext
from langalpha.backends.daytona import get_context_daytona_backend


class RuntimeToolInput(BaseModel):
    """Base schema that preserves ToolRuntime injection with explicit schemas."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: ToolRuntime[RunContext, object]


class DatasetMaterializeInput(RuntimeToolInput):
    logical_operation_id: str = Field(
        min_length=1,
        max_length=120,
        description="Stable logical ID reused when retrying the same materialization.",
    )
    name: str = Field(min_length=1, max_length=80)
    records: list[dict[str, Any]] | None = Field(
        default=None,
        description="Inline records for small/manual datasets.",
    )
    source_tool_call_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Preferred for large results: the prior ToolMessage call ID whose "
            "JSON content contains a records array."
        ),
    )
    source: str = Field(
        min_length=1,
        max_length=500,
        description="Tool/server/source identifier used to produce these records.",
    )
    file_format: Literal["jsonl", "csv"] = "jsonl"

    @model_validator(mode="after")
    def exactly_one_record_source(self) -> DatasetMaterializeInput:
        if (self.records is None) == (self.source_tool_call_id is None):
            raise ValueError("provide exactly one of records or source_tool_call_id")
        return self


class AskUserInput(RuntimeToolInput):
    question: str = Field(min_length=1, max_length=4_000)
    context: str | None = Field(default=None, max_length=8_000)


class PlanStep(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)


class SubmitPlanInput(RuntimeToolInput):
    goal: str = Field(min_length=1, max_length=2_000)
    steps: list[PlanStep] = Field(min_length=1, max_length=20)


class InspectAssetInput(RuntimeToolInput):
    path: str = Field(
        min_length=1,
        max_length=1_000,
        description="Absolute path below /workspace to inspect.",
    )
    preview_chars: int = Field(default=4_000, ge=0, le=8_000)


class ShowWidgetInput(RuntimeToolInput):
    kind: Literal["metric", "table", "bar", "line"]
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    data: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    x_field: str | None = Field(default=None, max_length=100)
    y_fields: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def chart_fields_are_explicit(self) -> ShowWidgetInput:
        if self.kind in {"bar", "line"} and (not self.x_field or not self.y_fields):
            raise ValueError("bar and line widgets require x_field and y_fields")
        return self


def _safe_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip(".-")
    if not name:
        raise ValueError("dataset name must contain a letter or number")
    return name[:80]


def _require_context(runtime: ToolRuntime[RunContext, object]) -> RunContext:
    context = runtime.context
    if not isinstance(context, RunContext):
        raise RuntimeError("server-issued RunContext is required")
    return context


def _tool_message_records(
    runtime: ToolRuntime[RunContext, object],
    tool_call_id: str,
) -> list[dict[str, Any]]:
    state = runtime.state
    if isinstance(state, dict):
        messages = state.get("messages", [])
    else:
        messages = getattr(state, "messages", [])
    fallback_candidates: list[list[dict[str, Any]]] = []
    for message in reversed(messages):
        message_call_id = (
            message.get("tool_call_id")
            if isinstance(message, dict)
            else getattr(message, "tool_call_id", None)
        )
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        if isinstance(content, list):
            text_blocks = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = "".join(text_blocks)
        if not isinstance(content, str):
            if message_call_id == tool_call_id:
                raise ValueError("source ToolMessage does not contain JSON text")
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            if message_call_id == tool_call_id:
                raise ValueError("source ToolMessage does not contain valid JSON") from None
            continue
        candidate = parsed.get("records") if isinstance(parsed, dict) else parsed
        if not isinstance(candidate, list) or not all(isinstance(row, dict) for row in candidate):
            if message_call_id == tool_call_id:
                raise ValueError("source ToolMessage JSON has no records array")
            continue
        if message_call_id == tool_call_id:
            return candidate
        fallback_candidates.append(candidate)
    if len(fallback_candidates) == 1:
        return fallback_candidates[0]
    if len(fallback_candidates) > 1:
        raise ValueError(
            f"source ToolMessage not found: {tool_call_id}; "
            "multiple record-producing ToolMessages make fallback ambiguous"
        )
    raise ValueError(f"source ToolMessage not found: {tool_call_id}")


@tool(args_schema=DatasetMaterializeInput)
def materialize_dataset(
    logical_operation_id: str,
    name: str,
    source: str,
    runtime: ToolRuntime[RunContext, object],
    records: list[dict[str, Any]] | None = None,
    source_tool_call_id: str | None = None,
    file_format: Literal["jsonl", "csv"] = "jsonl",
) -> str:
    """Materialize structured host-tool results into the workspace sandbox.

    Use this when a business/MCP tool returns enough rows that repeated model
    inspection would be wasteful. The returned absolute path can be consumed
    directly by Python or shell commands in the Daytona workspace.
    """

    _require_context(runtime)
    if (records is None) == (source_tool_call_id is None):
        raise ValueError("provide exactly one record source")
    resolved_records = (
        records
        if records is not None
        else _tool_message_records(runtime, source_tool_call_id or "")
    )
    safe_name = _safe_name(name)
    operation_id = _safe_name(logical_operation_id)
    if file_format == "jsonl":
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in resolved_records)
        columns = sorted({key for row in resolved_records for key in row})
    else:
        columns = sorted({key for row in resolved_records for key in row})
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(resolved_records)
        content = buffer.getvalue()

    payload = content.encode("utf-8")
    checksum = hashlib.sha256(payload).hexdigest()
    path = f"/workspace/input/{operation_id}/{safe_name}.{file_format}"
    backend = get_context_daytona_backend()
    backend.execute(f"mkdir -p /workspace/input/{operation_id}")
    result = backend.write(path, content)
    if result.error:
        existing = backend.read(path)
        file_data = existing.file_data if existing.error is None else None
        existing_content = file_data.get("content") if file_data else None
        if existing_content != content:
            raise RuntimeError("logical_operation_id already exists with different dataset content")
    return json.dumps(
        {
            "path": path,
            "format": file_format,
            "schema": {"columns": columns},
            "row_count": len(resolved_records),
            "source": source,
            "checksum": checksum,
        },
        ensure_ascii=False,
    )


@tool(args_schema=AskUserInput)
def ask_user(
    question: str,
    runtime: ToolRuntime[RunContext, object],
    context: str | None = None,
) -> str:
    """Pause the current run and ask the user for information required to continue."""

    _require_context(runtime)
    answer = interrupt(
        {
            "kind": "ask_user",
            "request_id": runtime.tool_call_id,
            "question": question,
            "context": context,
        }
    )
    return json.dumps({"answer": answer}, ensure_ascii=False, default=str)


@tool(args_schema=SubmitPlanInput)
def submit_plan(
    goal: str,
    steps: list[PlanStep],
    runtime: ToolRuntime[RunContext, object],
) -> str:
    """Pause the current run and request approval for a proposed execution plan."""

    _require_context(runtime)
    decision = interrupt(
        {
            "kind": "plan",
            "request_id": runtime.tool_call_id,
            "goal": goal,
            "steps": [step.model_dump(mode="json") for step in steps],
        }
    )
    return json.dumps({"decision": decision}, ensure_ascii=False, default=str)


@tool(args_schema=InspectAssetInput)
def inspect_asset(
    path: str,
    runtime: ToolRuntime[RunContext, object],
    preview_chars: int = 4_000,
) -> str:
    """Inspect a workspace asset without overriding Deep Agents read_file.

    Returns safe metadata and, for UTF-8 text assets, a bounded preview. Use
    this for uploaded or generated binary files that read_file cannot decode.
    """

    _require_context(runtime)
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/workspace/") or normalized != path:
        raise ValueError("asset path must be normalized and below /workspace")
    response = get_context_daytona_backend().download_files([normalized])[0]
    if response.error or response.content is None:
        raise FileNotFoundError(normalized)
    content = response.content
    if isinstance(content, str):
        payload = content.encode("utf-8")
        preview = content[:preview_chars] if preview_chars else None
    else:
        payload = bytes(content)
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError:
            preview = None
        else:
            preview = decoded[:preview_chars] if preview_chars else None
    result: dict[str, Any] = {
        "path": normalized,
        "name": posixpath.basename(normalized),
        "media_type": mimetypes.guess_type(normalized)[0] or "application/octet-stream",
        "size_bytes": len(payload),
        "checksum": hashlib.sha256(payload).hexdigest(),
    }
    if preview is not None:
        result["text_preview"] = preview
        result["preview_truncated"] = len(payload) > len(preview.encode("utf-8"))
    return json.dumps(result, ensure_ascii=False)


@tool(args_schema=ShowWidgetInput)
def show_widget(
    kind: Literal["metric", "table", "bar", "line"],
    title: str,
    runtime: ToolRuntime[RunContext, object],
    description: str | None = None,
    data: list[dict[str, Any]] | None = None,
    x_field: str | None = None,
    y_fields: list[str] | None = None,
) -> str:
    """Publish a bounded, structured data widget to the product UI."""

    context = _require_context(runtime)
    widget = {
        "id": f"{context.turn_id}:{runtime.tool_call_id}",
        "kind": kind,
        "title": title,
        "description": description,
        "data": data or [],
        "x_field": x_field,
        "y_fields": y_fields or [],
    }
    get_stream_writer()({"type": "widget.ready", "widget": widget})
    return json.dumps(widget, ensure_ascii=False)


HOST_TOOLS = [
    materialize_dataset,
    inspect_asset,
    show_widget,
    ask_user,
    submit_plan,
]
