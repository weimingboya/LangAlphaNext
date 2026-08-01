from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import langalpha.agent.factory as factory_module
from langalpha.agent.factory import (
    FILESYSTEM_PERMISSIONS,
    FILESYSTEM_TOOL_TOKEN_LIMIT,
    READ_FILE_TOOL_DESCRIPTION,
    RESEARCHER_PERMISSIONS,
    RESEARCHER_SKILLS,
    DeepAgentFactory,
)
from langalpha.agent.memory import MAIN_MEMORY_FILES
from langalpha.agent.prompts import MAIN_SYSTEM_PROMPT, RESEARCHER_SYSTEM_PROMPT
from langalpha.agent.responses import ResearchResult
from langalpha.config import get_settings


def test_factory_is_single_harness_entry_and_exposes_expected_tools(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    get_settings.cache_clear()
    graphs = importlib.import_module("langalpha.agent.graphs")

    tool_node = graphs.main_graph.get_graph().nodes["tools"].data
    names = set(tool_node._tools_by_name)
    assert {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
        "eval",
        "inspect_asset",
        "show_widget",
        "market_resolve_instrument",
        "market_get_bars",
        "market_get_corporate_actions",
        "sec_resolve_company",
        "sec_list_filings",
        "sec_get_filing",
        "sec_get_company_facts",
        "fred_search_series",
        "fred_get_observations",
        "ask_user",
        "start_async_task",
        "check_async_task",
        "update_async_task",
        "cancel_async_task",
        "list_async_tasks",
    } <= names
    assert "task" not in names
    assert "submit_plan" not in names
    assert "market_get_snapshots" not in names
    assert tool_node._tools_by_name["read_file"].description == READ_FILE_TOOL_DESCRIPTION

    researcher_tools = graphs.research_graph.get_graph().nodes["tools"].data
    researcher_names = set(researcher_tools._tools_by_name)
    assert {
        "market_resolve_instrument",
        "market_get_bars",
        "market_get_corporate_actions",
        "sec_resolve_company",
        "sec_list_filings",
        "sec_get_filing",
        "sec_get_company_facts",
        "fred_search_series",
        "fred_get_observations",
    } <= researcher_names
    assert {
        "task",
        "market_get_snapshots",
        "inspect_asset",
        "show_widget",
        "ask_user",
        "submit_plan",
        "start_async_task",
        "write_todos",
    }.isdisjoint(researcher_names)

    source_root = Path(__file__).resolve().parents[1] / "src"
    constructors = []
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "create_deep_agent(" in text:
            constructors.append(path.relative_to(source_root).as_posix())
        assert "create_agent(" not in text
    assert constructors == ["langalpha/agent/factory.py"]


def test_snapshot_tool_is_exposed_only_when_entitlement_is_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("MASSIVE_SNAPSHOTS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        graph = DeepAgentFactory().create("main")
        tools = graph.get_graph().nodes["tools"].data._tools_by_name
        assert "market_get_snapshots" in tools
    finally:
        get_settings.cache_clear()


def test_provider_native_tools_disable_quickjs_ptc(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("MCP_PTC_ALLOWLIST", '["external_tool"]')
    get_settings.cache_clear()
    captured_ptc: list[object] = []
    real_middleware = factory_module.CodeInterpreterMiddleware

    def capture_middleware(**kwargs):
        captured_ptc.append(kwargs.get("ptc"))
        return real_middleware(**kwargs)

    monkeypatch.setattr(
        factory_module,
        "CodeInterpreterMiddleware",
        capture_middleware,
    )
    try:
        DeepAgentFactory().create("main")
        assert captured_ptc == [None]
    finally:
        get_settings.cache_clear()


def test_factory_uses_tpm_aware_model_and_tool_retry_policies(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    get_settings.cache_clear()
    model_retry_kwargs: list[dict[str, object]] = []
    tool_retry_kwargs: list[dict[str, object]] = []
    real_model_retry = factory_module.ModelRetryMiddleware
    real_tool_retry = factory_module.ToolRetryMiddleware

    def capture_model_retry(**kwargs):
        model_retry_kwargs.append(kwargs)
        return real_model_retry(**kwargs)

    def capture_tool_retry(**kwargs):
        tool_retry_kwargs.append(kwargs)
        return real_tool_retry(**kwargs)

    monkeypatch.setattr(factory_module, "ModelRetryMiddleware", capture_model_retry)
    monkeypatch.setattr(factory_module, "ToolRetryMiddleware", capture_tool_retry)
    try:
        DeepAgentFactory().create("main")
    finally:
        get_settings.cache_clear()

    assert model_retry_kwargs == [
        {
            "max_retries": 3,
            "retry_on": factory_module.is_retryable_model_error,
            "initial_delay": 10,
            "max_delay": 60,
            "on_failure": "error",
        }
    ]
    assert len(tool_retry_kwargs) == 1
    assert tool_retry_kwargs[0]["retry_on"] is factory_module.is_retryable_tool_error


def test_factory_bounds_filesystem_results_with_native_middleware(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    get_settings.cache_clear()
    captured: list[dict[str, object]] = []
    real_middleware = factory_module.FilesystemMiddleware

    def capture_filesystem_middleware(**kwargs):
        captured.append(kwargs)
        return real_middleware(**kwargs)

    monkeypatch.setattr(
        factory_module,
        "FilesystemMiddleware",
        capture_filesystem_middleware,
    )
    try:
        DeepAgentFactory().create("main")
    finally:
        get_settings.cache_clear()

    assert len(captured) == 1
    assert captured[0]["tool_token_limit_before_evict"] == FILESYSTEM_TOOL_TOKEN_LIMIT
    assert captured[0]["custom_tool_descriptions"] == {"read_file": READ_FILE_TOOL_DESCRIPTION}
    assert captured[0]["_permissions"] == list(FILESYSTEM_PERMISSIONS)


def test_filesystem_permissions_protect_read_only_product_routes() -> None:
    snapshot = [
        {
            "operations": permission.operations,
            "paths": permission.paths,
            "mode": permission.mode,
        }
        for permission in FILESYSTEM_PERMISSIONS
    ]
    assert snapshot == [
        {
            "operations": ["write"],
            "paths": ["/skills/**", "/memory/**"],
            "mode": "deny",
        },
    ]
    researcher_snapshot = [
        {
            "operations": permission.operations,
            "paths": permission.paths,
            "mode": permission.mode,
        }
        for permission in RESEARCHER_PERMISSIONS
    ]
    assert researcher_snapshot == [
        {
            "operations": ["write"],
            "paths": ["/**"],
            "mode": "deny",
        },
    ]
    assert RESEARCHER_SKILLS == [
        "/skills/financial-research/",
        "/skills/sec-filing-analysis/",
    ]
    assert MAIN_MEMORY_FILES == [
        "/memory/AGENTS.md",
        "/memories/user/MEMORY.md",
        "/memories/project/MEMORY.md",
    ]
    assert set(ResearchResult.model_fields) == {
        "summary",
        "evidence",
        "datasets",
        "limitations",
    }


def test_agent_prompts_are_concise_english_and_memory_is_layered() -> None:
    assert not re.search(r"[\u3400-\u9fff]", MAIN_SYSTEM_PROMPT)
    assert not re.search(r"[\u3400-\u9fff]", RESEARCHER_SYSTEM_PROMPT)
    assert len(MAIN_SYSTEM_PROMPT) < 2_500
    assert len(RESEARCHER_SYSTEM_PROMPT) < 2_000
    assert "/memories/user/MEMORY.md" in MAIN_MEMORY_FILES
    assert "/memories/project/MEMORY.md" in MAIN_MEMORY_FILES


def test_langgraph_config_pins_verified_agent_server_version() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config = json.loads((project_root / "langgraph.json").read_text())
    assert config["api_version"] == "0.10.3"
    assert config["dependencies"] == ["."]
    assert set(config["graphs"]) == {"main", "researcher"}
