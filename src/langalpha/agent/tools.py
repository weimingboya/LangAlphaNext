from __future__ import annotations

import hashlib
import json
import mimetypes
import posixpath
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


def _require_context(runtime: ToolRuntime[RunContext, object]) -> RunContext:
    context = runtime.context
    if not isinstance(context, RunContext):
        raise RuntimeError("server-issued RunContext is required")
    return context


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
    inspect_asset,
    show_widget,
    ask_user,
]
