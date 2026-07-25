from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol

_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "resources" / "skills"


@lru_cache(maxsize=1)
def get_researcher_backend() -> BackendProtocol:
    """Load curated skills without provisioning a Daytona sandbox."""

    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/skills/": FilesystemBackend(root_dir=_SKILLS_ROOT, virtual_mode=True),
        },
    )
