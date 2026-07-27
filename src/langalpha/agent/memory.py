from __future__ import annotations

import logging

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

USER_MEMORY_PATH = "/memories/user/MEMORY.md"
PROJECT_MEMORY_PATH = "/memories/project/MEMORY.md"

MEMORY_TEMPLATES = {
    USER_MEMORY_PATH: """\
# User Memory

## Preferences

## Stable Context
""",
    PROJECT_MEMORY_PATH: """\
# Project Memory

## Objective

## Scope

## Conventions

## Decisions and Assumptions
""",
}

MAIN_MEMORY_FILES = [
    "/memory/AGENTS.md",
    USER_MEMORY_PATH,
    PROJECT_MEMORY_PATH,
]


class MemoryBootstrapMiddleware(AgentMiddleware):
    """Create small writable memory files before Deep Agents loads them."""

    def __init__(self, backend: BackendProtocol) -> None:
        self.backend = backend

    @staticmethod
    def _warn(path: str, error: str | None) -> None:
        if error and "already exists" not in error:
            logger.warning("Could not initialize memory file %s: %s", path, error)

    def before_agent(
        self,
        state: AgentState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> None:
        del state, runtime, config
        for path, content in MEMORY_TEMPLATES.items():
            try:
                result = self.backend.write(path, content)
            except Exception:
                logger.warning("Could not initialize memory file %s", path, exc_info=True)
                continue
            self._warn(path, result.error)

    async def abefore_agent(
        self,
        state: AgentState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> None:
        del state, runtime, config
        for path, content in MEMORY_TEMPLATES.items():
            try:
                result = await self.backend.awrite(path, content)
            except Exception:
                logger.warning("Could not initialize memory file %s", path, exc_info=True)
                continue
            self._warn(path, result.error)
