from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from langalpha.capabilities import materialization as module


async def test_materialize_text_writes_private_dataset_and_returns_reference(
    monkeypatch,
) -> None:
    writes: list[tuple[str, str]] = []

    class Backend:
        async def awrite(self, path: str, content: str) -> SimpleNamespace:
            writes.append((path, content))
            return SimpleNamespace(error=None, path=path)

    monkeypatch.setattr(module, "get_context_daytona_backend", lambda: Backend())
    path = module.dataset_path("sec", "0000320193", "facts.jsonl")
    result = await module.materialize_text(path, '{"value":1}\n', format="jsonl")

    assert writes == [(path, '{"value":1}\n')]
    assert result == {
        "path": path,
        "format": "jsonl",
        "encoding": "utf-8",
        "size_bytes": 12,
        "sha256": hashlib.sha256(b'{"value":1}\n').hexdigest(),
    }


def test_dataset_path_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        module.dataset_path("sec", "..", "secret")
