from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain.tools import ToolRuntime
from pydantic import ValidationError

from langalpha.agent import tools as module
from langalpha.agent.context import RunContext


def _runtime() -> ToolRuntime[RunContext, dict]:
    return ToolRuntime(
        context=RunContext(
            project_id="project",
            owner_id="owner",
            workspace_id="workspace",
            product_thread_id="thread",
            turn_id="turn",
            product_run_id="run",
        ),
        tool_call_id="tool-call",
        state={"messages": []},
        config={},
        stream_writer=lambda _: None,
        store=None,
    )


def test_dataset_materializer_is_idempotent_and_returns_stable_reference(
    monkeypatch,
) -> None:
    files: dict[str, str] = {}

    class Backend:
        def execute(self, _: str):
            return SimpleNamespace(exit_code=0, output="")

        def write(self, path: str, content: str):
            if path in files:
                return SimpleNamespace(error="exists")
            files[path] = content
            return SimpleNamespace(error=None)

        def read(self, path: str):
            return SimpleNamespace(
                error=None,
                file_data={"content": files[path]},
            )

    monkeypatch.setattr(module, "get_context_daytona_backend", lambda: Backend())
    kwargs = {
        "logical_operation_id": "quotes-2026-07-24",
        "name": "quotes",
        "records": [{"symbol": "AAPL", "price": 200.0}],
        "source": "market.quote",
        "runtime": _runtime(),
        "file_format": "jsonl",
    }
    first = json.loads(module.materialize_dataset.func(**kwargs))
    second = json.loads(module.materialize_dataset.func(**kwargs))

    assert first == second
    assert first["path"] == ("/workspace/input/quotes-2026-07-24/quotes.jsonl")
    assert first["row_count"] == 1
    assert first["schema"] == {"columns": ["price", "symbol"]}
    assert len(first["checksum"]) == 64


def test_dataset_materializer_reads_prior_tool_message_without_record_copy(
    monkeypatch,
) -> None:
    files: dict[str, str] = {}

    class Backend:
        def execute(self, _: str):
            return SimpleNamespace(exit_code=0, output="")

        def write(self, path: str, content: str):
            files[path] = content
            return SimpleNamespace(error=None)

    monkeypatch.setattr(module, "get_context_daytona_backend", lambda: Backend())
    runtime = _runtime()
    runtime.state["messages"] = [
        SimpleNamespace(
            tool_call_id="market-call",
            content=json.dumps(
                {
                    "records": [
                        {"symbol": "AAPL", "price": 200.0},
                        {"symbol": "MSFT", "price": 500.0},
                    ]
                }
            ),
        )
    ]
    result = json.loads(
        module.materialize_dataset.func(
            logical_operation_id="market-call",
            name="quotes",
            source="get_market_quotes",
            source_tool_call_id="market-call",
            runtime=runtime,
            file_format="jsonl",
        )
    )
    assert result["row_count"] == 2
    assert files[result["path"]].count("\n") == 2


def test_inspect_asset_returns_bounded_metadata(monkeypatch) -> None:
    content = b"symbol,price\nAAPL,200\n"

    class Backend:
        def download_files(self, paths: list[str]):
            assert paths == ["/workspace/uploads/holdings.csv"]
            return [SimpleNamespace(error=None, content=content)]

    monkeypatch.setattr(module, "get_context_daytona_backend", lambda: Backend())
    result = json.loads(
        module.inspect_asset.func(
            path="/workspace/uploads/holdings.csv",
            runtime=_runtime(),
            preview_chars=12,
        )
    )
    assert result["media_type"] == "text/csv"
    assert result["size_bytes"] == len(content)
    assert result["text_preview"] == "symbol,price"
    assert result["preview_truncated"] is True
    with pytest.raises(ValueError, match="normalized"):
        module.inspect_asset.func(
            path="/workspace/uploads/../secret",
            runtime=_runtime(),
        )


def test_show_widget_emits_structured_custom_event(monkeypatch) -> None:
    emitted: list[dict] = []
    monkeypatch.setattr(module, "get_stream_writer", lambda: emitted.append)
    result = json.loads(
        module.show_widget.func(
            kind="metric",
            title="Portfolio return",
            description="Latest calculated value",
            data=[{"label": "Return", "value": 12.4}],
            x_field=None,
            y_fields=["value"],
            runtime=_runtime(),
        )
    )
    assert result["id"] == "run:tool-call"
    assert emitted == [{"type": "widget.ready", "widget": result}]


def test_chart_widget_schema_requires_explicit_axes() -> None:
    with pytest.raises(ValidationError, match="x_field and y_fields"):
        module.ShowWidgetInput(
            kind="line",
            title="Incomplete chart",
            data=[{"date": "2026-07-24", "value": 1}],
            runtime=_runtime(),
        )

    valid = module.ShowWidgetInput(
        kind="bar",
        title="Return comparison",
        data=[{"symbol": "AAPL", "return": 1.2}],
        x_field="symbol",
        y_fields=["return"],
        runtime=_runtime(),
    )
    assert valid.x_field == "symbol"


def test_explicit_tool_schemas_preserve_hidden_runtime_injection() -> None:
    for runtime_tool in module.HOST_TOOLS:
        assert "runtime" in runtime_tool.args_schema.model_fields
        properties = runtime_tool.tool_call_schema.model_json_schema()["properties"]
        assert "runtime" not in properties
