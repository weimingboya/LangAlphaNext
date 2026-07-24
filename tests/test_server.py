from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from langalpha.config import Settings
from langalpha.server import agent_gateway as gateway_module
from langalpha.server import main as main_module


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FakeThreads:
    def __init__(self) -> None:
        self.state: dict = {"values": {"messages": [], "todos": []}, "interrupts": []}
        self.created: list[dict] = []

    async def create(self, **kwargs: object) -> dict[str, str]:
        self.created.append(kwargs)
        return {"thread_id": "graph-thread"}

    async def get_state(self, *_: object, **__: object) -> dict:
        return self.state

    async def get_history(self, *_: object, **__: object) -> list[dict]:
        return [self.state]


class FakeRuns:
    def __init__(self, threads: FakeThreads) -> None:
        self.threads = threads
        self.created: list[dict] = []
        self.items: dict[str, dict] = {}
        self.last_event_ids: list[str | None] = []

    async def create(self, *_: object, **kwargs: object) -> dict:
        self.created.append(kwargs)
        run_id = f"graph-run-{len(self.created)}"
        remote = {
            "run_id": run_id,
            "status": "running",
            "metadata": kwargs.get("metadata", {}),
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.items[run_id] = remote
        if kwargs.get("command") is not None:
            self.threads.state["interrupts"] = []
        return {**remote, "status": "pending"}

    async def get(self, _: str, run_id: str, **__: object) -> dict:
        return self.items[run_id]

    async def list(self, *_: object, **__: object) -> list[dict]:
        return list(reversed(self.items.values()))

    async def join_stream(self, _: str, run_id: str, **kwargs: object):
        self.last_event_ids.append(kwargs.get("last_event_id"))
        widget = {
            "id": "widget-1",
            "kind": "metric",
            "title": "Portfolio return",
            "data": [{"return_percent": 4.2}],
            "x_field": None,
            "y_fields": ["return_percent"],
        }
        yield SimpleNamespace(
            event="custom",
            data={"type": "sandbox.bound", "sandbox_id": "sandbox-id"},
            id="event-1",
        )
        yield SimpleNamespace(
            event="custom",
            data={
                "type": "artifact.changed",
                "path": "/workspace/artifacts/report.md",
                "name": "report.md",
                "media_type": "text/markdown",
                "size_bytes": 31,
                "checksum": "report-checksum",
            },
            id="event-2",
        )
        yield SimpleNamespace(
            event="custom",
            data={"type": "widget.ready", "widget": widget},
            id="event-3",
        )
        yield SimpleNamespace(
            event="messages/complete",
            data={
                "value": [
                    {
                        "id": "assistant-message",
                        "type": "ai",
                        "content": "The report is ready.",
                        "usage_metadata": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    }
                ]
            },
            id="event-4",
        )
        self.items[run_id]["status"] = "success"
        self.items[run_id]["updated_at"] = _now()
        self.threads.state = {
            "values": {
                "messages": [
                    {"id": "user-message", "type": "human", "content": "Analyze this"},
                    {
                        "id": "assistant-message",
                        "type": "ai",
                        "content": "The report is ready.",
                        "usage_metadata": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    },
                    {
                        "id": "widget-message",
                        "type": "tool",
                        "role": "tool",
                        "name": "show_widget",
                        "content": json.dumps(widget),
                    },
                ],
                "todos": [{"content": "Analyze", "status": "completed"}],
            },
            "interrupts": [],
        }

    async def cancel(self, _: str, run_id: str, **__: object) -> None:
        self.items[run_id]["status"] = "interrupted"
        self.items[run_id]["updated_at"] = _now()


class FakeGraphClient:
    def __init__(self) -> None:
        self.threads = FakeThreads()
        self.runs = FakeRuns(self.threads)


class FakeBackend:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.id = "sandbox-id"

    def execute(self, command: str, **_: object):
        if 'Path("/workspace/artifacts")' in command:
            lines = []
            for path, content in sorted(self.files.items()):
                if path.startswith("/workspace/artifacts/"):
                    lines.append(
                        json.dumps(
                            {
                                "path": path,
                                "size_bytes": len(content),
                                "checksum": "manifest-checksum",
                            }
                        )
                    )
            return SimpleNamespace(exit_code=0, output="\n".join(lines))
        return SimpleNamespace(exit_code=0, output="")

    def upload_files(self, files: list[tuple[str, bytes]]):
        for path, content in files:
            self.files[path] = content
        return [SimpleNamespace(path=path, error=None) for path, _ in files]

    def download_files(self, paths: list[str]):
        return [
            SimpleNamespace(path=path, content=self.files.get(path), error=None) for path in paths
        ]


def _client(tmp_path, monkeypatch) -> tuple[TestClient, FakeGraphClient, FakeBackend]:
    graph = FakeGraphClient()
    backend = FakeBackend()
    monkeypatch.setattr(gateway_module, "get_client", lambda **_: graph)
    monkeypatch.setattr(
        main_module,
        "get_daytona_backend_for_workspace",
        lambda **_: backend,
    )
    settings = Settings(
        OPENAI_API_KEY="test",
        LANGALPHA_DATABASE_PATH=tmp_path / "app.db",
        OPENAI_INPUT_COST_PER_MILLION=1,
        OPENAI_OUTPUT_COST_PER_MILLION=2,
    )
    return TestClient(main_module.create_app(settings)), graph, backend


def _thread(client: TestClient) -> dict:
    response = client.post("/api/threads", json={"title": "Research"})
    response.raise_for_status()
    return response.json()


def _run(client: TestClient, thread: dict) -> dict:
    response = client.post(
        f"/api/threads/{thread['id']}/runs",
        json={"message": "Analyze this"},
    )
    response.raise_for_status()
    return response.json()


def test_agent_server_is_the_only_run_authority(tmp_path, monkeypatch) -> None:
    client, graph, _ = _client(tmp_path, monkeypatch)
    with client:
        thread = _thread(client)
        run = _run(client, thread)

        submitted = graph.runs.created[0]
        assert submitted["multitask_strategy"] == "reject"
        assert submitted["stream_resumable"] is True
        assert submitted["context"]["workspace_id"] == thread["workspace_id"]
        assert submitted["context"]["product_run_id"] == run["control_id"]
        assert submitted["metadata"]["turn_id"] == run["turn_id"]
        assert run["id"].startswith("graph-run-")
        assert run["id"] != run["control_id"]

        current = client.get(f"/api/threads/{thread['id']}/runs/{run['id']}").json()
        listed = client.get(f"/api/threads/{thread['id']}/runs").json()
        assert current["status"] == "running"
        assert [item["id"] for item in listed] == [run["id"]]

        with sqlite3.connect(tmp_path / "app.db") as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        assert not tables & {"product_runs", "domain_events", "event_outbox"}
        assert client.get(f"/api/runs/{run['id']}").status_code == 404
        assert client.get(f"/api/threads/{thread['id']}/events").status_code == 404


def test_resumable_stream_is_proxied_and_product_side_effects_are_projected(
    tmp_path, monkeypatch
) -> None:
    client, graph, backend = _client(tmp_path, monkeypatch)
    backend.files["/workspace/artifacts/report.md"] = b"# Portfolio report\nReturn: 4.2%"
    with client:
        thread = _thread(client)
        run = _run(client, thread)
        with client.stream(
            "GET",
            f"/api/threads/{thread['id']}/runs/{run['id']}/stream",
            headers={"Last-Event-ID": "cursor-0"},
        ) as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "event: sandbox.bound" in body
        assert "event: artifact.updated" in body
        assert "event: widget.ready" in body
        assert "event: message.completed" in body
        assert "event: run.success" in body
        assert f"id: terminal:{run['id']}:success" in body
        assert graph.runs.last_event_ids == ["cursor-0"]

        binding = client.get(f"/api/threads/{thread['id']}/binding").json()
        assert binding["sandbox_id"] == "sandbox-id"
        artifacts = client.get(f"/api/threads/{thread['id']}/artifacts").json()
        report = next(item for item in artifacts if item["name"] == "report.md")
        assert report["run_id"] == run["id"]
        assert client.get(f"/api/artifacts/{report['id']}").content.startswith(
            b"# Portfolio report"
        )


def test_snapshot_rebuilds_messages_todos_widgets_usage_runs_and_artifacts(
    tmp_path, monkeypatch
) -> None:
    client, _, backend = _client(tmp_path, monkeypatch)
    backend.files["/workspace/artifacts/report.md"] = b"report"
    with client:
        thread = _thread(client)
        run = _run(client, thread)
        with client.stream(
            "GET",
            f"/api/threads/{thread['id']}/runs/{run['id']}/stream",
        ) as response:
            assert response.status_code == 200
            _ = "".join(response.iter_text())

        snapshot = client.get(f"/api/threads/{thread['id']}/snapshot").json()
        assert [message["role"] for message in snapshot["messages"]] == [
            "user",
            "assistant",
            "tool",
        ]
        assert snapshot["todos"] == [{"content": "Analyze", "status": "completed"}]
        assert snapshot["widgets"][0]["title"] == "Portfolio return"
        assert snapshot["usage"]["total_tokens"] == 120
        assert snapshot["usage"]["estimated_cost_usd"] == 0.00014
        assert snapshot["runs"][0]["status"] == "success"
        assert snapshot["artifacts"][0]["sandbox_path"] == "/workspace/artifacts/report.md"


def test_interrupt_resume_cancel_and_guidance_use_graph_run_ids(tmp_path, monkeypatch) -> None:
    client, graph, _ = _client(tmp_path, monkeypatch)
    with client:
        thread = _thread(client)
        run = _run(client, thread)
        graph.runs.items[run["id"]]["status"] = "success"
        graph.threads.state["interrupts"] = [
            {"id": "interrupt-1", "value": {"question": "Which market?"}}
        ]

        interrupted = client.get(f"/api/threads/{thread['id']}/runs/{run['id']}").json()
        assert interrupted["status"] == "interrupted"
        blocked = client.post(
            f"/api/threads/{thread['id']}/runs",
            json={"message": "Start another"},
        )
        assert blocked.status_code == 409

        resumed_response = client.post(
            f"/api/threads/{thread['id']}/runs/{run['id']}/resume",
            json={"value": "US equities"},
        )
        resumed_response.raise_for_status()
        resumed = resumed_response.json()
        assert resumed["parent_run_id"] == run["id"]
        assert resumed["turn_id"] == run["turn_id"]
        assert graph.runs.created[-1]["command"] == {"resume": "US equities"}

        graph.runs.items[resumed["id"]]["status"] = "running"
        guidance_response = client.post(
            f"/api/threads/{thread['id']}/runs/{resumed['id']}/guidance",
            json={"message": "Prioritize cash flow"},
        )
        guidance_response.raise_for_status()
        guidance = guidance_response.json()
        assert guidance["run_id"] == resumed["control_id"]
        claimed = client.post(f"/internal/runs/{resumed['control_id']}/guidance/claim").json()
        assert [item["id"] for item in claimed] == [guidance["id"]]

        cancelled = client.post(f"/api/threads/{thread['id']}/runs/{resumed['id']}/cancel")
        assert cancelled.status_code == 204
        current = client.get(f"/api/threads/{thread['id']}/runs/{resumed['id']}").json()
        assert current["status"] == "cancelled"


def test_file_upload_is_on_demand_and_downloadable(tmp_path, monkeypatch) -> None:
    client, _, backend = _client(tmp_path, monkeypatch)
    with client:
        thread = _thread(client)
        response = client.post(
            f"/api/threads/{thread['id']}/files",
            files={"file": ("holdings.csv", b"symbol,weight\nAAPL,0.1\n", "text/csv")},
        )
        response.raise_for_status()
        artifact = response.json()
        assert artifact["sandbox_path"] in backend.files
        assert client.get(f"/api/artifacts/{artifact['id']}").content == (
            b"symbol,weight\nAAPL,0.1\n"
        )
