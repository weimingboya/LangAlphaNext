from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunContext:
    project_id: str
    owner_id: str
    workspace_id: str
    product_thread_id: str
    turn_id: str
    product_run_id: str
    capability_profile: str = "main"
    expected_sandbox_id: str | None = None
