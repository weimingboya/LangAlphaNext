from __future__ import annotations

from evals.fixtures import FIXTURE_RESEARCH_TOOLS
from langalpha.agent.factory import DeepAgentFactory
from langalpha.agent.tools import ask_user

_factory = DeepAgentFactory()

research_graph = _factory.create(
    "researcher",
    tools=FIXTURE_RESEARCH_TOOLS,
)
main_graph = _factory.create(
    "main",
    tools=[ask_user, *FIXTURE_RESEARCH_TOOLS],
    include_async_subagents=True,
)
