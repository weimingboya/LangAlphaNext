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


class ProductRun(BaseModel):
    id: str
    thread_id: str
    graph_run_id: str | None = None
    turn_id: str
    parent_run_id: str | None = None
    status: RunStatus
    cancel_requested: bool = False
    error: str | None = None
    created_at: datetime
    updated_at: datetime


Delivery = Literal["durable", "volatile"]


class DomainEvent(BaseModel):
    schema_version: int = 1
    delivery: Delivery = "durable"
    sequence: int
    id: str
    source_event_key: str
    project_id: str
    workspace_id: str
    thread_id: str
    turn_id: str | None = None
    run_id: str | None
    type: str
    source: dict[str, str | None] = Field(
        default_factory=lambda: {"agent_id": "main", "parent_agent_id": None}
    )
    payload: dict[str, Any]
    created_at: datetime


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
