from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ThreadCreate(BaseModel):
    title: str = Field(default="New research", min_length=1, max_length=200)


class ThreadPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ThreadView(BaseModel):
    id: str
    title: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


RunStrategy = Literal["enqueue", "interrupt"]


class RunCreate(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    strategy: RunStrategy = "enqueue"
    input_asset_ids: list[str] = Field(default_factory=list, max_length=50)


RunStatus = Literal[
    "pending",
    "running",
    "success",
    "error",
    "interrupted",
    "cancelled",
]


class RunView(BaseModel):
    id: str
    thread_id: str
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
    web_search_calls: int = 0
    estimated_cost_usd: float | None = None


AssetRole = Literal["input", "artifact", "dataset", "workspace"]
AssetStatus = Literal["uploading", "ready", "failed", "deleted"]
RetentionClass = Literal["temporary", "standard", "pinned"]


class Asset(BaseModel):
    id: str
    owner_id: str
    thread_id: str
    turn_id: str | None = None
    role: AssetRole
    status: AssetStatus
    logical_key: str
    bucket_id: str
    object_path: str
    sandbox_path: str | None = None
    filename: str
    media_type: str
    size_bytes: int | None = None
    sha256: str | None = None
    retention_class: RetentionClass = "standard"
    created_at: datetime
    updated_at: datetime


class AssetUploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class AssetUploadTicket(BaseModel):
    asset: Asset
    signed_url: str
    token: str
    tus_endpoint: str


class AssetComplete(BaseModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class AssetDownloadTicket(BaseModel):
    url: str
    expires_in: int


class PublicConfig(BaseModel):
    supabase_url: str
    supabase_publishable_key: str
    storage_bucket: str
    max_upload_bytes: int


class ThreadSnapshot(BaseModel):
    """Reloadable UI projection assembled from authoritative resources."""

    thread: ThreadView
    runs: list[RunView]
    messages: list[dict[str, Any]]
    todos: list[dict[str, Any]]
    interrupts: list[Any]
    widgets: list[dict[str, Any]]
    usage: UsageSummary
    assets: list[Asset]


class ResumeRun(BaseModel):
    value: Any
