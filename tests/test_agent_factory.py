from __future__ import annotations

import importlib
import json
from pathlib import Path

from langalpha.agent.factory import FILESYSTEM_PERMISSIONS
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
        "glob",
        "grep",
        "execute",
        "task",
        "eval",
        "materialize_dataset",
        "inspect_asset",
        "show_widget",
        "market_resolve_instrument",
        "market_get_snapshots",
        "market_get_bars",
        "market_get_corporate_actions",
        "sec_resolve_company",
        "sec_list_filings",
        "sec_get_filing",
        "sec_get_company_facts",
        "fred_search_series",
        "fred_get_observations",
        "ask_user",
        "submit_plan",
        "start_async_task",
        "check_async_task",
        "update_async_task",
        "cancel_async_task",
        "list_async_tasks",
    } <= names

    source_root = Path(__file__).resolve().parents[1] / "src"
    constructors = []
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "create_deep_agent(" in text:
            constructors.append(path.relative_to(source_root).as_posix())
        assert "create_agent(" not in text
    assert constructors == ["langalpha/agent/factory.py"]


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
            "paths": ["/skills/**", "/memory/**", "/memos/**"],
            "mode": "deny",
        },
    ]


def test_langgraph_config_pins_verified_agent_server_version() -> None:
    project_root = Path(__file__).resolve().parents[1]
    config = json.loads((project_root / "langgraph.json").read_text())
    assert config["api_version"] == "0.10.3"
    assert config["dependencies"] == ["."]
    assert set(config["graphs"]) == {"main", "researcher"}
