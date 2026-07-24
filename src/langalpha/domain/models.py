from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ThreadCreate(BaseModel):
    title: str = Field(default="New research", min_length=1, max_length=200)


class ProductThread(BaseModel):
    id: str
    graph_thread_id: str
    workspace_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class RunCreate(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)


RunStatus = Literal[
    "pending",
    "running",
    "success",
    "error",
    "interrupted",
    "cancelled",
]


class RunView(BaseModel):
    """Product-shaped view of an Agent Server run.

    ``id`` is the Agent Server run ID. ``control_id`` is an opaque per-run
    identifier used only by in-process steering middleware.
    """

    id: str
    thread_id: str
    control_id: str
    turn_id: str
    parent_run_id: str | None = None
    status: RunStatus
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentEvent(BaseModel):
    """Transient UI event proxied from an Agent Server resumable stream."""

    id: str
    thread_id: str
    run_id: str
    type: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)


class UsageSummary(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    estimated_cost_usd: float | None = None


class Artifact(BaseModel):
    id: str
    thread_id: str
    run_id: str | None = None
    name: str
    sandbox_path: str
    media_type: str
    size_bytes: int
    checksum: str | None = None
    created_at: datetime
    updated_at: datetime


class RuntimeBinding(BaseModel):
    project_id: str
    owner_id: str
    workspace_id: str
    product_thread_id: str
    runtime_thread_id: str
    assistant_id: str
    kernel_version: str
    profile: str
    sandbox_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ThreadSnapshot(BaseModel):
    """Reloadable UI projection assembled from authoritative resources."""

    thread: ProductThread
    runs: list[RunView]
    messages: list[dict[str, Any]]
    todos: list[dict[str, Any]]
    interrupts: list[Any]
    widgets: list[dict[str, Any]]
    usage: UsageSummary
    artifacts: list[Artifact]


class ResumeRun(BaseModel):
    value: Any


class GuidanceCreate(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class Guidance(BaseModel):
    id: str
    thread_id: str
    run_id: str
    message: str
    status: Literal["accepted", "delivered", "returned", "reclaimed"]
    created_at: datetime
    updated_at: datetime


class GuidanceReturn(BaseModel):
    ids: list[str] = Field(max_length=100)
