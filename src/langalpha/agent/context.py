from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunContext:
    project_id: str
    owner_id: str
    thread_id: str
    turn_id: str
    input_asset_ids: tuple[str, ...] = ()
    expected_sandbox_id: str | None = None
    app_id: str = "langalpha"
