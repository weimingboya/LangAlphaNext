from __future__ import annotations

import asyncio

from deepagents.backends.protocol import WriteResult

from langalpha.agent.memory import MEMORY_TEMPLATES, MemoryBootstrapMiddleware


class FakeBackend:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def write(self, path: str, content: str) -> WriteResult:
        if path in self.files:
            return WriteResult(error=f"Cannot write to {path} because it already exists.")
        self.files[path] = content
        return WriteResult(path=path)

    async def awrite(self, path: str, content: str) -> WriteResult:
        return self.write(path, content)


def test_memory_bootstrap_creates_templates_without_overwriting_existing_memory() -> None:
    backend = FakeBackend()
    middleware = MemoryBootstrapMiddleware(backend)  # type: ignore[arg-type]

    middleware.before_agent({}, None, {})  # type: ignore[arg-type]
    assert backend.files == MEMORY_TEMPLATES

    backend.files["/memories/user/MEMORY.md"] += "\n- Prefers concise answers.\n"
    middleware.before_agent({}, None, {})  # type: ignore[arg-type]

    assert backend.files["/memories/user/MEMORY.md"].endswith("- Prefers concise answers.\n")


def test_async_memory_bootstrap_uses_the_same_idempotent_templates() -> None:
    backend = FakeBackend()
    middleware = MemoryBootstrapMiddleware(backend)  # type: ignore[arg-type]

    asyncio.run(middleware.abefore_agent({}, None, {}))  # type: ignore[arg-type]

    assert backend.files == MEMORY_TEMPLATES


def test_memory_bootstrap_failure_is_non_blocking() -> None:
    class FailingBackend(FakeBackend):
        def write(self, path: str, content: str) -> WriteResult:
            raise RuntimeError("store unavailable")

    middleware = MemoryBootstrapMiddleware(FailingBackend())  # type: ignore[arg-type]

    middleware.before_agent({}, None, {})  # type: ignore[arg-type]
