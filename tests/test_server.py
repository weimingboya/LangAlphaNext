from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from langalpha.config import Settings
from langalpha.server import main as main_module
from langalpha.server import stream_bridge as bridge_module


class FakeThreads:
    async def create(self, **_: object) -> dict[str, str]:
        return {"thread_id": "graph-thread"}

    async def get_state(self, *_: object, **__: object) -> dict:
        return {
            "values": {
                "messages": [
                    {
                        "id": "assistant-message",
                        "type": "ai",
                        "content": "done",
                        "usage_metadata": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    }
                ]
            }
        }

    async def get_history(self, *_: object, **__: object) -> list[dict]:
        return []


class FakeRuns:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.status = "success"

    async def create(self, *_: object, **__: object) -> dict[str, str]:
        self.created.append(__)
        return {"run_id": f"graph-run-{len(self.created)}"}

    async def join_stream(self, *_: object, **__: object):
        yield SimpleNamespace(event="metadata", data={"attempt": 1}, id="event-1")
        yield SimpleNamespace(event="updates", data={"model": "done"}, id="event-2")

    async def get(self, *_: object, **__: object) -> dict[str, str]:
        return {"status": self.status}

    async def cancel(self, *_: object, **__: object) -> None:
        self.status = "interrupted"


class FakeGraphClient:
    def __init__(self) -> None:
        self.threads = FakeThreads()
        self.runs = FakeRuns()


class FlakyRuns(FakeRuns):
    def __init__(self) -> None:
        super().__init__()
        self.join_calls = 0
        self.last_event_ids: list[str | None] = []

    async def join_stream(self, *_: object, **kwargs: object):
        self.join_calls += 1
        cursor = kwargs.get("last_event_id")
        self.last_event_ids.append(str(cursor) if cursor is not None else None)
        yield SimpleNamespace(event="metadata", data={"attempt": 1}, id="event-1")
        if self.join_calls == 1:
            raise ConnectionError("temporary stream disconnect")
        yield SimpleNamespace(event="updates", data={"model": "done"}, id="event-2")

    async def get(self, *_: object, **__: object) -> dict[str, str]:
        return {"status": "running" if self.join_calls == 1 else "success"}


class FlakyGraphClient(FakeGraphClient):
    def __init__(self) -> None:
        super().__init__()
        self.runs = FlakyRuns()


class FailingRuns(FakeRuns):
    async def create(self, *_: object, **__: object) -> dict[str, str]:
        raise ConnectionError("Agent Server unavailable")


class FailingGraphClient(FakeGraphClient):
    def __init__(self) -> None:
        super().__init__()
        self.runs = FailingRuns()


class GoldenThreads(FakeThreads):
    async def get_state(self, *_: object, **__: object) -> dict:
        return {
            "values": {
                "messages": [
                    {
                        "id": "golden-message",
                        "type": "ai",
                        "content": "The portfolio report is ready.",
                        "usage_metadata": {
                            "input_tokens": 250,
                            "output_tokens": 80,
                            "total_tokens": 330,
                        },
                    }
                ],
                "todos": [
                    {"content": "Load symbols", "status": "completed"},
                    {"content": "Calculate returns", "status": "completed"},
                ],
            }
        }


class GoldenRuns(FakeRuns):
    async def join_stream(self, *_: object, **__: object):
        artifact = {
            "type": "artifact.changed",
            "path": "/workspace/artifacts/report.md",
            "name": "report.md",
            "media_type": "text/markdown",
            "size_bytes": 37,
            "checksum": "golden-checksum",
        }
        widget = {
            "type": "widget.ready",
            "widget": {
                "id": "golden-widget",
                "kind": "metric",
                "title": "Portfolio return",
                "data": [{"return_percent": 4.2}],
                "x_field": None,
                "y_fields": ["return_percent"],
            },
        }
        yield SimpleNamespace(event="metadata", data={"attempt": 1}, id="event-1")
        yield SimpleNamespace(
            event="custom", data={"type": "sandbox.bound", "sandbox_id": "sandbox-id"}
        )
        yield SimpleNamespace(event="custom", data=artifact)
        yield SimpleNamespace(event="custom", data=artifact)
        yield SimpleNamespace(event="custom", data=widget)
        yield SimpleNamespace(event="custom", data=widget)
        yield SimpleNamespace(
            event="messages/complete",
            data={
                "value": [
                    {
                        "id": "golden-message",
                        "type": "ai",
                        "content": "The portfolio report is ready.",
                        "usage_metadata": {
                            "input_tokens": 250,
                            "output_tokens": 80,
                            "total_tokens": 330,
                        },
                    }
                ]
            },
            id="event-2",
        )


class GoldenGraphClient(FakeGraphClient):
    def __init__(self) -> None:
        super().__init__()
        self.threads = GoldenThreads()
        self.runs = GoldenRuns()


class MultiUsageThreads(FakeThreads):
    async def get_state(self, *_: object, **__: object) -> dict:
        return {
            "values": {
                "messages": [
                    {
                        "id": "usage-message-2",
                        "type": "ai",
                        "content": "Second step",
                        "usage_metadata": {
                            "input_tokens": 50,
                            "output_tokens": 10,
                            "total_tokens": 60,
                        },
                    }
                ]
            }
        }


class MultiUsageRuns(FakeRuns):
    async def join_stream(self, *_: object, **__: object):
        for index in (1, 2):
            yield SimpleNamespace(
                event="messages/complete",
                data={
                    "value": [
                        {
                            "id": f"usage-message-{index}",
                            "type": "ai",
                            "content": f"Step {index}",
                            "usage_metadata": {
                                "input_tokens": 50,
                                "output_tokens": 10,
                                "total_tokens": 60,
                            },
                        }
                    ]
                },
                id=f"event-{index}",
            )


class MultiUsageGraphClient(FakeGraphClient):
    def __init__(self) -> None:
        super().__init__()
        self.threads = MultiUsageThreads()
        self.runs = MultiUsageRuns()


class FakeBackend:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.id = "sandbox-id"

    def execute(self, _: str):
        return None

    def write(self, path: str, content: bytes):
        self.files[path] = content
        return None

    def upload_files(self, files: list[tuple[str, bytes]]):
        for path, content in files:
            self.files[path] = content
        return [SimpleNamespace(path=path, error=None) for path, _ in files]

    def download_files(self, paths: list[str]):
        return [
            SimpleNamespace(path=path, content=self.files.get(path), error=None) for path in paths
        ]


def _client(
    tmp_path,
    monkeypatch,
    graph: FakeGraphClient | None = None,
    **settings_overrides,
) -> tuple[TestClient, FakeBackend]:
    graph = graph or FakeGraphClient()
    backend = FakeBackend()
    monkeypatch.setattr(main_module, "get_client", lambda **_: graph)
    monkeypatch.setattr(bridge_module, "get_client", lambda **_: graph)
    monkeypatch.setattr(
        main_module,
        "get_daytona_backend_for_workspace",
        lambda **_: backend,
    )
    settings = Settings(
        OPENAI_API_KEY="test",
        LANGALPHA_DATABASE_PATH=tmp_path / "app.db",
        **settings_overrides,
    )
    return TestClient(main_module.create_app(settings)), backend


def test_thread_run_and_static_ui(tmp_path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        assert client.get("/health").json() == {"status": "ok"}
        assert "LangAlpha" in client.get("/").text

        thread = client.post("/api/threads", json={"title": "Test research"}).json()
        run = client.post(
            f"/api/threads/{thread['id']}/runs",
            json={"message": "Analyze this"},
        ).json()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = client.get(f"/api/runs/{run['id']}").json()
            repository = client.app.state.repository
            events = asyncio.run(repository.list_events(thread["id"]))
            if current["status"] == "success" and any(
                event.type == "run.success" for event in events
            ):
                break
            time.sleep(0.02)
        assert current["status"] == "success"

        assert [event.type for event in events] == [
            "user.message",
            "run.started",
            "agent.metadata",
            "agent.state.updated",
            "message.completed",
            "usage.updated",
            "run.success",
        ]
        assert run["id"] == current["id"]
        assert all(event.source_event_key for event in events)


def test_run_context_ignores_browser_supplied_identity(tmp_path, monkeypatch) -> None:
    graph = FakeGraphClient()
    client, _ = _client(tmp_path, monkeypatch, graph)
    with client:
        thread = client.post("/api/threads", json={"title": "Server identity"}).json()
        response = client.post(
            f"/api/threads/{thread['id']}/runs",
            json={
                "message": "Use the server-issued context",
                "workspace_id": "attacker-workspace",
                "owner_id": "attacker-owner",
                "product_run_id": "attacker-run",
                "context": {"project_id": "attacker-project"},
            },
        )
        assert response.status_code == 202

        submitted = graph.runs.created[0]
        assert submitted["context"]["workspace_id"] == thread["workspace_id"]
        assert submitted["context"]["owner_id"] == "local-user"
        assert submitted["context"]["project_id"] == "langalpha-local"
        assert submitted["context"]["product_run_id"] == response.json()["id"]
        assert "attacker" not in str(submitted)


def test_usage_emits_one_cost_warning_at_configured_threshold(tmp_path, monkeypatch) -> None:
    client, _ = _client(
        tmp_path,
        monkeypatch,
        MultiUsageGraphClient(),
        OPENAI_INPUT_COST_PER_MILLION=10_000,
        OPENAI_OUTPUT_COST_PER_MILLION=10_000,
        COST_WARNING_USD=1,
    )
    with client:
        thread = client.post("/api/threads", json={"title": "Cost warning"}).json()
        client.post(
            f"/api/threads/{thread['id']}/runs",
            json={"message": "Use enough test tokens"},
        ).raise_for_status()
        repository = client.app.state.repository
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            events = asyncio.run(repository.list_events(thread["id"]))
            if any(event.type == "run.success" for event in events):
                break
            time.sleep(0.02)

        warnings = [event for event in events if event.type == "cost.warning"]
        assert sum(event.type == "usage.updated" for event in events) == 2
        assert len(warnings) == 1
        assert warnings[0].payload == {
            "estimated_run_cost_usd": 1.2,
            "threshold_usd": 1.0,
            "model": None,
        }


def test_usage_reads_agent_server_completed_message_shape() -> None:
    usage = bridge_module._usage_dict(
        {
            "usage_metadata": None,
            "additional_kwargs": {
                "usage_metadata": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    "input_token_details": {"cache_read": 80},
                }
            },
            "response_metadata": {"model_name": "gpt-5.6-luna"},
        }
    )

    assert usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cached_input_tokens": 80,
        "model": "gpt-5.6-luna",
    }


def test_file_upload_is_on_demand_and_downloadable(tmp_path, monkeypatch) -> None:
    client, backend = _client(tmp_path, monkeypatch)
    with client:
        thread = client.post("/api/threads", json={"title": "Files"}).json()
        response = client.post(
            f"/api/threads/{thread['id']}/files",
            files={"file": ("holdings.csv", b"symbol,weight\nAAPL,0.1\n", "text/csv")},
        )
        assert response.status_code == 201
        artifact = response.json()
        assert artifact["name"] == "holdings.csv"
        assert artifact["sandbox_path"] in backend.files
        binding = client.get(f"/api/threads/{thread['id']}/binding").json()
        assert binding["sandbox_id"] == "sandbox-id"

        downloaded = client.get(f"/api/artifacts/{artifact['id']}")
        assert downloaded.status_code == 200
        assert downloaded.content == b"symbol,weight\nAAPL,0.1\n"

        artifacts = client.get(f"/api/threads/{thread['id']}/artifacts").json()
        assert [item["id"] for item in artifacts] == [artifact["id"]]


def test_golden_research_flow_projects_artifact_widget_usage_and_replay(
    tmp_path, monkeypatch
) -> None:
    client, backend = _client(tmp_path, monkeypatch, GoldenGraphClient())
    report = b"# Portfolio report\n\nReturn: 4.2%\n"
    backend.files["/workspace/artifacts/report.md"] = report
    with client:
        thread = client.post("/api/threads", json={"title": "Golden research"}).json()
        upload = client.post(
            f"/api/threads/{thread['id']}/files",
            files={"file": ("stocks.csv", b"symbol\nAAPL\nMSFT\n", "text/csv")},
        )
        assert upload.status_code == 201

        run = client.post(
            f"/api/threads/{thread['id']}/runs",
            json={"message": "Compare the uploaded symbols and write a report"},
        ).json()
        repository = client.app.state.repository
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            events = asyncio.run(repository.list_events(thread["id"]))
            if any(event.type == "run.success" for event in events):
                break
            time.sleep(0.02)

        assert len({event.id for event in events}) == len(events)
        event_types = [event.type for event in events]
        assert event_types.count("artifact.created") == 1
        assert event_types.count("artifact.updated") == 1
        assert event_types.count("widget.ready") == 1
        assert event_types.count("message.completed") == 1
        assert event_types.count("usage.updated") == 1
        assert event_types.count("todo.updated") == 1
        assert event_types.count("run.success") == 1

        artifacts = client.get(f"/api/threads/{thread['id']}/artifacts").json()
        generated = next(item for item in artifacts if item["name"] == "report.md")
        downloaded = client.get(f"/api/artifacts/{generated['id']}")
        assert downloaded.content == report

        replay = asyncio.run(repository.list_events(thread["id"], after_sequence=0))
        assert [event.id for event in replay] == [event.id for event in events]
        tail = asyncio.run(
            repository.list_events(
                thread["id"],
                after_sequence=events[-2].sequence,
            )
        )
        assert [event.type for event in tail] == ["run.success"]
        assert client.get(f"/api/runs/{run['id']}").json()["status"] == "success"


@pytest.mark.parametrize("_attempt", range(20))
def test_stream_disconnect_rejoins_from_cursor_without_duplicate_events(
    tmp_path, monkeypatch, _attempt
) -> None:
    graph = FlakyGraphClient()
    client, _ = _client(tmp_path, monkeypatch, graph)
    with client:
        thread = client.post("/api/threads", json={"title": "Reconnect"}).json()
        run = client.post(
            f"/api/threads/{thread['id']}/runs",
            json={"message": "Finish after reconnect"},
        ).json()

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = client.get(f"/api/runs/{run['id']}").json()
            events = asyncio.run(client.app.state.repository.list_events(thread["id"]))
            if current["status"] == "success" and any(
                event.type == "run.success" for event in events
            ):
                break
            time.sleep(0.02)

        assert current["status"] == "success"
        assert graph.runs.join_calls == 2
        assert graph.runs.last_event_ids == [None, "event-1"]
        event_types = [event.type for event in events]
        assert event_types.count("agent.metadata") == 1
        assert event_types.count("run.success") == 1
        assert "run.error" not in event_types


def test_run_create_failure_is_durable_and_does_not_leave_pending_run(
    tmp_path, monkeypatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch, FailingGraphClient())
    with client:
        thread = client.post("/api/threads", json={"title": "Create failure"}).json()
        response = client.post(
            f"/api/threads/{thread['id']}/runs",
            json={"message": "This cannot start"},
        )
        assert response.status_code == 502

        events = asyncio.run(client.app.state.repository.list_events(thread["id"]))
        assert [event.type for event in events] == [
            "user.message",
            "run.error",
        ]
        failed = asyncio.run(client.app.state.repository.get_run(events[-1].run_id))
        assert failed is not None
        assert failed.status == "error"
        assert failed.graph_run_id is None


def test_new_turn_is_rejected_while_thread_has_active_run(tmp_path, monkeypatch) -> None:
    graph = FakeGraphClient()
    client, _ = _client(tmp_path, monkeypatch, graph)
    with client:
        thread = client.post("/api/threads", json={"title": "Single run"}).json()
        repository = client.app.state.repository
        active = asyncio.run(
            repository.create_run(
                thread_id=thread["id"],
                graph_run_id="active-runtime-run",
                run_id="active-product-run",
                turn_id="active-turn",
                status="running",
            )
        )
        client.app.state.bridge.remote_status = AsyncMock(side_effect=lambda _thread, run: run)

        response = client.post(
            f"/api/threads/{thread['id']}/runs",
            json={"message": "Start another"},
        )
        assert response.status_code == 409
        assert "active run" in response.text
        assert active.status == "running"
        assert graph.runs.created == []


def test_cancel_intent_maps_remote_interrupt_to_one_cancelled_terminal(
    tmp_path, monkeypatch
) -> None:
    graph = FakeGraphClient()
    graph.runs.status = "running"
    client, _ = _client(tmp_path, monkeypatch, graph)
    with client:
        thread = client.post("/api/threads", json={"title": "Cancel"}).json()
        repository = client.app.state.repository
        run = asyncio.run(
            repository.create_run(
                thread_id=thread["id"],
                graph_run_id="cancel-runtime-run",
                run_id="cancel-product-run",
                turn_id="cancel-turn",
                status="running",
            )
        )

        response = client.post(f"/api/runs/{run.id}/cancel")
        assert response.status_code == 204

        current = client.get(f"/api/runs/{run.id}").json()
        assert current["status"] == "cancelled"
        assert current["cancel_requested"] is True

        events = asyncio.run(repository.list_events(thread["id"]))
        assert [event.type for event in events] == ["run.cancelled"]


def test_successful_runtime_with_checkpoint_interrupt_maps_to_interrupted(
    tmp_path, monkeypatch
) -> None:
    graph = FakeGraphClient()
    interrupt = {
        "id": "interrupt-id",
        "value": {"kind": "ask_user", "question": "Which market?"},
    }

    async def interrupted_state(*_: object, **__: object) -> dict:
        return {
            "values": {"messages": []},
            "interrupts": [interrupt],
        }

    async def interrupt_stream(*_: object, **__: object):
        yield SimpleNamespace(
            event="updates",
            data={"__interrupt__": [interrupt]},
            id="interrupt-event",
        )

    graph.threads.get_state = interrupted_state
    graph.runs.join_stream = interrupt_stream
    client, _ = _client(tmp_path, monkeypatch, graph)
    with client:
        thread = client.post("/api/threads", json={"title": "Interrupted"}).json()
        response = client.post(
            f"/api/threads/{thread['id']}/runs",
            json={"message": "Ask a question"},
        )
        response.raise_for_status()
        repository = client.app.state.repository
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = client.get(f"/api/runs/{response.json()['id']}").json()
            events = asyncio.run(repository.list_events(thread["id"]))
            if any(event.type == "run.interrupted" for event in events):
                break
            time.sleep(0.02)

        assert current["status"] == "interrupted"
        assert sum(event.type == "interrupt.requested" for event in events) == 1


def test_guidance_claim_return_and_interrupt_resume(tmp_path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        thread = client.post("/api/threads", json={"title": "Interactive"}).json()
        repository = client.app.state.repository
        running = asyncio.run(
            repository.create_run(
                thread_id=thread["id"],
                graph_run_id="running-runtime-run",
                run_id="running-product-run",
                turn_id="running-turn",
                status="running",
            )
        )
        interrupted = asyncio.run(
            repository.create_run(
                thread_id=thread["id"],
                graph_run_id="interrupted-runtime-run",
                run_id="interrupted-product-run",
                turn_id="turn",
                status="interrupted",
            )
        )
        client.app.state.bridge.remote_status = AsyncMock(side_effect=lambda _thread, run: run)

        guidance_response = client.post(
            f"/api/runs/{running.id}/guidance",
            json={"message": "Prioritize cash-flow evidence"},
        )
        assert guidance_response.status_code == 202
        guidance = guidance_response.json()
        claimed = client.post(f"/internal/runs/{running.id}/guidance/claim").json()
        assert [item["id"] for item in claimed] == [guidance["id"]]
        assert claimed[0]["status"] == "delivered"
        returned = client.post(
            f"/internal/runs/{running.id}/guidance/return",
            json={"ids": [guidance["id"]]},
        )
        assert returned.status_code == 204
        asyncio.run(repository.update_run(running.id, "success"))

        resumed = client.post(
            f"/api/runs/{interrupted.id}/resume",
            json={"value": {"decision": "approve"}},
        )
        assert resumed.status_code == 202
        resumed_run = resumed.json()
        assert resumed_run["parent_run_id"] == interrupted.id
        assert resumed_run["turn_id"] == interrupted.turn_id
        assert resumed_run["id"] != interrupted.id
