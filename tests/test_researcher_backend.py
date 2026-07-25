from __future__ import annotations

from deepagents.backends import StateBackend

from langalpha.backends.researcher import get_researcher_backend


def test_researcher_backend_loads_curated_skills_without_a_sandbox() -> None:
    get_researcher_backend.cache_clear()
    backend = get_researcher_backend()

    assert isinstance(backend.default, StateBackend)
    listing = backend.ls("/skills/")
    assert listing.error is None
    paths = {item["path"] for item in listing.entries or []}
    assert "/skills/financial-research/" in paths
    assert "/skills/sec-filing-analysis/" in paths

    skill = backend.read("/skills/sec-filing-analysis/SKILL.md")
    assert skill.error is None
    assert "SEC filing analysis" in skill.file_data["content"]
