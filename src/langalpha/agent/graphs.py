from __future__ import annotations

from langalpha.agent.factory import DeepAgentFactory

_factory = DeepAgentFactory()

research_graph = _factory.create("researcher")
report_graph = _factory.create("reporter")
main_graph = _factory.create("main", include_async_subagents=True)
