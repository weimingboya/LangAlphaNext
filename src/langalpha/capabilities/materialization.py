from __future__ import annotations

import hashlib
import re
from typing import Any

from langalpha.backends.daytona import get_context_daytona_backend

DATASET_ROOT = "/workspace/.langalpha/datasets"
_PATH_PART = re.compile(r"^[A-Za-z0-9._-]+$")


def dataset_path(provider: str, *parts: str) -> str:
    """Build a stable private workspace path for a provider-owned dataset."""
    components = (provider, *parts)
    if not all(
        component not in {".", ".."} and _PATH_PART.fullmatch(component)
        for component in components
    ):
        raise ValueError("dataset path contains unsupported characters")
    return "/".join((DATASET_ROOT, *components))


async def materialize_text(path: str, content: str, *, format: str) -> dict[str, Any]:
    """Persist content outside model context and return a compact data reference."""
    if not path.startswith(f"{DATASET_ROOT}/"):
        raise ValueError("datasets must be stored below the private dataset root")
    result = await get_context_daytona_backend().awrite(path, content)
    if result.error is not None or result.path is None:
        raise RuntimeError(result.error or f"failed to materialize dataset at {path}")
    payload = content.encode("utf-8")
    return {
        "path": path,
        "format": format,
        "encoding": "utf-8",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
