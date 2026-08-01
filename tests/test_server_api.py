from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from langalpha.config import Settings
from langalpha.domain.models import (
    Asset,
    AssetDownloadTicket,
    AssetUploadTicket,
    ProjectView,
    ThreadView,
)
from langalpha.server.agent_gateway import AgentGateway, run_view
from langalpha.server.auth import AuthenticationError, AuthUser
from langalpha.server.main import create_app


def _now() -> datetime:
    return datetime.now(UTC)


class FakeAuthenticator:
    def authenticate(self, access_token: str) -> AuthUser:
        users = {
            "valid": AuthUser(id="00000000-0000-0000-0000-000000000001", email="a@example.com"),
            "other": AuthUser(id="00000000-0000-0000-0000-000000000002", email="b@example.com"),
        }
        if access_token not in users:
            raise AuthenticationError("invalid or expired access token")
        return users[access_token]


class FakeAssets:
    def __init__(self) -> None:
        self.items: dict[str, tuple[Asset, bytes]] = {}

    def _asset(
        self,
        *,
        owner_id: str,
        project_id: str,
        filename: str,
        media_type: str,
        content: bytes,
        status: str = "ready",
    ) -> Asset:
        asset_id = str(uuid4())
        checksum = hashlib.sha256(content).hexdigest()
        return Asset(
            id=asset_id,
            owner_id=owner_id,
            project_id=project_id,
            role="input",
            status=status,
            logical_key=f"input:{asset_id}",
            object_path=f"{owner_id}/{project_id}/{asset_id}/{checksum}/{filename}",
            filename=filename,
            media_type=media_type,
            size_bytes=len(content),
            sha256=checksum,
            created_at=_now(),
            updated_at=_now(),
        )

    def create_upload(self, *, owner_id: str, project_id: str, request):
        asset = self._asset(
            owner_id=owner_id,
            project_id=project_id,
            filename=request.filename,
            media_type=request.media_type,
            content=b"x" * request.size_bytes,
            status="uploading",
        )
        asset.sha256 = request.sha256
        self.items[asset.id] = (asset, b"x" * request.size_bytes)
        return AssetUploadTicket(
            asset=asset,
            bucket_name="langalpha-assets",
            signed_url="https://storage.test/upload",
            token="signed-token",
            tus_endpoint="https://storage.test/tus",
        )

    def complete_upload(self, *, owner_id: str, asset_id: str, sha256: str) -> Asset:
        asset, content = self.items[asset_id]
        if asset.owner_id != owner_id or asset.sha256 != sha256:
            raise ValueError("invalid completion")
        asset.status = "ready"
        self.items[asset_id] = (asset, content)
        return asset

    def ingest_input(
        self,
        *,
        owner_id: str,
        project_id: str,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> Asset:
        asset = self._asset(
            owner_id=owner_id,
            project_id=project_id,
            filename=filename,
            media_type=media_type,
            content=content,
        )
        self.items[asset.id] = (asset, content)
        return asset

    def get_asset(self, *, owner_id: str, asset_id: str) -> Asset:
        asset = self.items[asset_id][0]
        if asset.owner_id != owner_id or asset.status == "deleted":
            raise LookupError("asset not found")
        return asset

    def list_assets(self, *, owner_id: str, project_id: str) -> list[Asset]:
        return [
            asset
            for asset, _ in self.items.values()
            if asset.owner_id == owner_id
            and asset.project_id == project_id
            and asset.status != "deleted"
        ]

    def download_ticket(
        self,
        *,
        owner_id: str,
        asset_id: str,
        expires_in: int = 300,
    ) -> AssetDownloadTicket:
        self.get_asset(owner_id=owner_id, asset_id=asset_id)
        return AssetDownloadTicket(url="https://storage.test/download", expires_in=expires_in)

    def download_bytes(self, *, owner_id: str, asset_id: str) -> tuple[Asset, bytes]:
        asset = self.get_asset(owner_id=owner_id, asset_id=asset_id)
        return asset, self.items[asset_id][1]

    def delete_asset(self, *, owner_id: str, asset_id: str) -> None:
        asset = self.get_asset(owner_id=owner_id, asset_id=asset_id)
        asset.status = "deleted"

    def require_ready_inputs(
        self,
        *,
        owner_id: str,
        project_id: str,
        asset_ids: list[str],
    ) -> list[Asset]:
        assets = [self.get_asset(owner_id=owner_id, asset_id=item) for item in asset_ids]
        if any(asset.project_id != project_id or asset.status != "ready" for asset in assets):
            raise ValueError("invalid input asset")
        return assets


class FakeProjects:
    def __init__(self) -> None:
        self.items: dict[str, ProjectView] = {}

    def healthcheck(self) -> None:
        return None

    def create_project(self, *, owner_id: str, name: str) -> ProjectView:
        project = ProjectView(
            id=str(uuid4()),
            owner_id=owner_id,
            name=name,
            status="active",
            created_at=_now(),
            updated_at=_now(),
        )
        self.items[project.id] = project
        return project

    def get_project(self, *, owner_id: str, project_id: str) -> ProjectView:
        project = self.items[project_id]
        if project.owner_id != owner_id or project.status == "deleted":
            raise LookupError("project not found")
        return project

    def list_projects(self, *, owner_id: str) -> list[ProjectView]:
        return [
            project
            for project in self.items.values()
            if project.owner_id == owner_id and project.status != "deleted"
        ]

    def rename_project(self, *, owner_id: str, project_id: str, name: str) -> ProjectView:
        project = self.get_project(owner_id=owner_id, project_id=project_id)
        project.name = name
        project.updated_at = _now()
        return project

    def bind_sandbox(self, *, owner_id: str, project_id: str, sandbox_id: str) -> ProjectView:
        project = self.get_project(owner_id=owner_id, project_id=project_id)
        project.sandbox_id = sandbox_id
        return project

    def replace_sandbox(
        self,
        *,
        owner_id: str,
        project_id: str,
        expected_sandbox_id: str,
        sandbox_id: str,
    ) -> ProjectView:
        project = self.get_project(owner_id=owner_id, project_id=project_id)
        if project.sandbox_id == expected_sandbox_id:
            project.sandbox_id = sandbox_id
        return project

    def mark_deleting(self, *, owner_id: str, project_id: str) -> ProjectView:
        project = self.get_project(owner_id=owner_id, project_id=project_id)
        project.status = "deleting"
        return project

    def delete_project(self, *, owner_id: str, project_id: str) -> None:
        project = self.items[project_id]
        assert project.owner_id == owner_id
        project.status = "deleted"


class FakeStreamRuns:
    def __init__(self, gateway: FakeGateway) -> None:
        self.gateway = gateway
        self.last_event_id: str | None = None

    async def get(self, thread_id: str, run_id: str) -> dict:
        return self.gateway.remote_runs[thread_id][run_id]

    async def join_stream(self, thread_id: str, run_id: str, **kwargs: object):
        self.last_event_id = kwargs.get("last_event_id")  # type: ignore[assignment]
        yield SimpleNamespace(
            event="custom",
            data={"type": "sandbox.bound", "sandbox_id": "sandbox-1"},
            id="event-1",
        )
        yield SimpleNamespace(
            event="custom",
            data={
                "type": "asset.ready",
                "id": "asset-output",
                "sandbox_path": "/workspace/artifacts/report.html",
            },
            id="event-2",
        )
        self.gateway.remote_runs[thread_id][run_id]["status"] = "success"


class FakeGateway:
    def __init__(self) -> None:
        self.threads: dict[str, ThreadView] = {}
        self.remote_runs: dict[str, dict[str, dict]] = {}
        self.states: dict[str, dict] = {}
        self.histories: dict[str, list[dict]] = {}
        self.submitted: list[dict] = []
        self.updated_states: list[dict] = []
        self.client = SimpleNamespace(runs=FakeStreamRuns(self))

    async def healthcheck(self, assistant_id: str) -> None:
        assert assistant_id == "main"

    async def create_thread(self, *, metadata: dict, thread_id: str | None = None) -> ThreadView:
        now = _now()
        thread = ThreadView(
            id=thread_id or str(uuid4()),
            title=metadata["title"],
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )
        self.threads[thread.id] = thread
        self.remote_runs[thread.id] = {}
        self.states[thread.id] = {"values": {"messages": [], "todos": []}, "interrupts": []}
        self.histories[thread.id] = []
        return thread

    async def search_threads(self, *, metadata: dict, **_: object) -> list[ThreadView]:
        return [
            thread
            for thread in self.threads.values()
            if all(thread.metadata.get(key) == value for key, value in metadata.items())
        ]

    async def get_thread(self, thread_id: str) -> ThreadView:
        return self.threads[thread_id]

    async def update_thread_metadata(self, thread_id: str, updates: dict) -> ThreadView:
        thread = self.threads[thread_id]
        thread.metadata.update(updates)
        thread.title = str(thread.metadata["title"])
        thread.updated_at = _now()
        return thread

    async def delete_thread(self, thread_id: str) -> None:
        del self.threads[thread_id]

    async def state(self, thread_id: str, *, checkpoint_id: str | None = None) -> dict:
        if checkpoint_id:
            return next(
                state
                for state in self.histories[thread_id]
                if state.get("checkpoint", {}).get("checkpoint_id") == checkpoint_id
            )
        return self.states[thread_id]

    async def history(self, thread_id: str, **_: object) -> list[dict]:
        return self.histories[thread_id]

    async def update_state(
        self,
        thread_id: str,
        *,
        checkpoint: dict,
        values: dict,
    ) -> dict:
        checkpoint_id = f"fork-{uuid4()}"
        self.updated_states.append(
            {
                "thread_id": thread_id,
                "checkpoint": checkpoint,
                "values": values,
            }
        )
        return {
            "checkpoint": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }

    async def run_metadata(self, thread_id: str, run_id: str) -> dict:
        return self.remote_runs[thread_id][run_id]["metadata"]

    async def create(self, thread_id: str, assistant_id: str, **kwargs: object) -> dict:
        run_id = str(uuid4())
        remote = {
            "run_id": run_id,
            "status": "pending",
            "metadata": kwargs["metadata"],
            "created_at": _now().isoformat(),
            "updated_at": _now().isoformat(),
        }
        self.remote_runs[thread_id][run_id] = remote
        self.submitted.append(
            {
                "thread_id": thread_id,
                "assistant_id": assistant_id,
                **kwargs,
            }
        )
        if kwargs.get("command") is not None:
            self.states[thread_id]["interrupts"] = []
        return remote

    async def run(self, thread_id: str, run_id: str):
        remote = self.remote_runs[thread_id][run_id]
        return run_view(
            remote,
            thread_id=thread_id,
            has_checkpoint_interrupt=bool(self.states[thread_id]["interrupts"]),
        )

    async def runs(self, thread_id: str, **_: object):
        return [
            run_view(remote, thread_id=thread_id)
            for remote in reversed(self.remote_runs[thread_id].values())
        ]

    async def cancel(self, thread_id: str, run_id: str) -> None:
        self.remote_runs[thread_id][run_id]["status"] = "interrupted"


def _headers(token: str = "valid") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_agent_gateway_passes_deployment_api_key(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("langalpha.server.agent_gateway.get_client", fake_get_client)
    AgentGateway("https://agent.example", api_key="deployment-key")
    assert captured == {
        "url": "https://agent.example",
        "api_key": "deployment-key",
    }


async def test_agent_gateway_healthcheck_resolves_graph_name(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAssistants:
        async def search(self, **kwargs: object) -> list[dict[str, str]]:
            captured.update(kwargs)
            return [{"assistant_id": "00000000-0000-0000-0000-000000000001"}]

    monkeypatch.setattr(
        "langalpha.server.agent_gateway.get_client",
        lambda **_: SimpleNamespace(assistants=FakeAssistants()),
    )

    await AgentGateway("https://agent.example").healthcheck("main")

    assert captured == {"graph_id": "main", "limit": 1}


def test_ready_rejects_unreachable_dependencies() -> None:
    client, gateway, _ = _client()

    async def unavailable(_assistant_id: str) -> None:
        raise RuntimeError("offline")

    gateway.healthcheck = unavailable  # type: ignore[method-assign]
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["unavailable"] == ["LANGGRAPH"]


def _client() -> tuple[TestClient, FakeGateway, FakeAssets]:
    gateway = FakeGateway()
    assets = FakeAssets()
    projects = FakeProjects()
    settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="test",
        DAYTONA_API_KEY="test",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_PUBLISHABLE_KEY="publishable",
        SUPABASE_SECRET_KEY="secret",
        OPENAI_INPUT_COST_PER_MILLION=1,
        OPENAI_OUTPUT_COST_PER_MILLION=2,
    )
    client = TestClient(
        create_app(
            settings,
            gateway=gateway,  # type: ignore[arg-type]
            authenticator=FakeAuthenticator(),
            asset_store=assets,
            project_store=projects,
        )
    )
    return client, gateway, assets


def _project(client: TestClient, token: str = "valid", name: str = "Test Project") -> dict:
    response = client.post(
        "/api/projects",
        json={"name": name},
        headers=_headers(token),
    )
    response.raise_for_status()
    return response.json()


def _thread(
    client: TestClient,
    token: str = "valid",
    project_id: str | None = None,
) -> dict:
    project_id = project_id or _project(client, token)["id"]
    response = client.post(
        f"/api/projects/{project_id}/threads",
        json={"title": "Research"},
        headers=_headers(token),
    )
    response.raise_for_status()
    return response.json()


def test_projects_scope_threads_and_support_lifecycle() -> None:
    client, _, _ = _client()
    first = _project(client, name="Alpha")
    second = _project(client, name="Beta")
    first_thread = _thread(client, project_id=first["id"])
    second_thread = _thread(client, project_id=second["id"])

    first_threads = client.get(
        f"/api/projects/{first['id']}/threads",
        headers=_headers(),
    )
    first_threads.raise_for_status()
    assert [thread["id"] for thread in first_threads.json()] == [first_thread["id"]]
    assert second_thread["id"] not in {thread["id"] for thread in first_threads.json()}
    assert (
        client.get(
            f"/api/projects/{first['id']}",
            headers=_headers("other"),
        ).status_code
        == 404
    )

    renamed = client.patch(
        f"/api/projects/{first['id']}",
        json={"name": "Alpha renamed"},
        headers=_headers(),
    )
    renamed.raise_for_status()
    assert renamed.json()["name"] == "Alpha renamed"

    empty = _project(client, name="Disposable")
    deleted = client.delete(f"/api/projects/{empty['id']}", headers=_headers())
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{empty['id']}", headers=_headers()).status_code == 404


def test_auth_and_thread_metadata_are_the_authorization_boundary() -> None:
    client, _, _ = _client()
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    assert client.get("/api/projects").status_code == 401

    thread = _thread(client)
    assert thread["id"]
    assert thread["metadata"] == {
        "schema_version": 2,
        "app_id": "langalpha",
        "project_id": thread["metadata"]["project_id"],
        "owner_id": "00000000-0000-0000-0000-000000000001",
        "thread_kind": "main",
        "title": "Research",
    }
    assert client.get(f"/api/threads/{thread['id']}", headers=_headers("other")).status_code == 404

    renamed = client.patch(
        f"/api/threads/{thread['id']}",
        json={"title": "Renamed"},
        headers=_headers(),
    )
    renamed.raise_for_status()
    assert renamed.json()["title"] == "Renamed"


def test_native_run_strategy_metadata_and_input_assets() -> None:
    client, gateway, assets = _client()
    thread = _thread(client)
    asset = assets.ingest_input(
        owner_id=thread["metadata"]["owner_id"],
        project_id=thread["metadata"]["project_id"],
        filename="data.csv",
        media_type="text/csv",
        content=b"value\n1\n",
    )

    response = client.post(
        f"/api/threads/{thread['id']}/runs",
        json={
            "message": "Analyze this",
            "strategy": "interrupt",
            "input_asset_ids": [asset.id],
        },
        headers=_headers(),
    )
    response.raise_for_status()
    run = response.json()
    submitted = gateway.submitted[0]

    assert submitted["strategy"] == "interrupt"
    assert submitted["context"]["thread_id"] == thread["id"]
    assert submitted["context"]["input_asset_ids"] == [asset.id]
    assert submitted["metadata"]["owner_id"] == thread["metadata"]["owner_id"]
    assert submitted["metadata"]["thread_id"] == thread["id"]
    assert submitted["metadata"]["turn_id"] == run["turn_id"]
    assert "/workspace/input/assets/" in submitted["input"]["messages"][0]["content"]


def test_edit_latest_message_forks_from_its_input_checkpoint() -> None:
    client, gateway, _ = _client()
    thread = _thread(client)
    original_run = client.post(
        f"/api/threads/{thread['id']}/runs",
        json={"message": "Original question"},
        headers=_headers(),
    )
    original_run.raise_for_status()
    run_id = original_run.json()["id"]
    user_message = {
        "id": "user-1",
        "role": "user",
        "content": "Original question",
    }
    input_state = {
        "values": {"messages": [user_message], "todos": []},
        "checkpoint": {
            "thread_id": thread["id"],
            "checkpoint_ns": "",
            "checkpoint_id": "input-1",
        },
        "parent_checkpoint": None,
        "metadata": {"run_id": run_id, "source": "input"},
        "created_at": _now().isoformat(),
        "interrupts": [],
    }
    final_state = {
        "values": {
            "messages": [
                user_message,
                {"id": "answer-1", "role": "assistant", "content": "Original answer"},
            ],
            "todos": [],
        },
        "checkpoint": {
            "thread_id": thread["id"],
            "checkpoint_ns": "",
            "checkpoint_id": "final-1",
        },
        "parent_checkpoint": input_state["checkpoint"],
        "metadata": {"run_id": run_id, "source": "loop"},
        "created_at": _now().isoformat(),
        "interrupts": [],
    }
    gateway.histories[thread["id"]] = [final_state, input_state]
    gateway.states[thread["id"]] = final_state

    edited = client.post(
        f"/api/threads/{thread['id']}/runs",
        json={
            "message": "Edited question",
            "branch_checkpoint_id": "final-1",
            "edit_latest": True,
        },
        headers=_headers(),
    )
    edited.raise_for_status()

    assert gateway.updated_states == [
        {
            "thread_id": thread["id"],
            "checkpoint": input_state["checkpoint"],
            "values": {
                "messages": [
                    {
                        "id": "user-1",
                        "role": "user",
                        "content": "Edited question",
                    }
                ]
            },
        }
    ]
    submitted = gateway.submitted[-1]
    assert submitted["input"] is None
    assert submitted["checkpoint"]["checkpoint_id"].startswith("fork-")
    assert submitted["metadata"]["edit_latest"] is True
    assert submitted["metadata"]["source_run_id"] == run_id


def test_thread_delete_cancels_runs_and_removes_assets() -> None:
    client, gateway, assets = _client()
    thread = _thread(client)
    asset = assets.ingest_input(
        owner_id=thread["metadata"]["owner_id"],
        project_id=thread["metadata"]["project_id"],
        filename="delete-me.txt",
        media_type="text/plain",
        content=b"temporary",
    )
    run_response = client.post(
        f"/api/threads/{thread['id']}/runs",
        json={"message": "Research this"},
        headers=_headers(),
    )
    run_response.raise_for_status()
    run_id = run_response.json()["id"]

    deleted = client.delete(f"/api/threads/{thread['id']}", headers=_headers())

    assert deleted.status_code == 204
    assert thread["id"] not in gateway.threads
    assert gateway.remote_runs[thread["id"]][run_id]["status"] == "interrupted"
    assert assets.items[asset.id][0].status == "ready"


def test_snapshot_stream_resume_and_cancel_use_agent_server_state() -> None:
    client, gateway, assets = _client()
    thread = _thread(client)
    asset = assets.ingest_input(
        owner_id=thread["metadata"]["owner_id"],
        project_id=thread["metadata"]["project_id"],
        filename="notes.txt",
        media_type="text/plain",
        content=b"notes",
    )
    run_response = client.post(
        f"/api/threads/{thread['id']}/runs",
        json={"message": "Research"},
        headers=_headers(),
    )
    run_response.raise_for_status()
    run = run_response.json()

    stream = client.get(
        f"/api/threads/{thread['id']}/runs/{run['id']}/stream",
        headers={**_headers(), "Last-Event-ID": "event-0"},
    )
    stream.raise_for_status()
    assert "event: sandbox.bound" in stream.text
    assert "event: asset.ready" in stream.text
    assert "event: run.success" in stream.text
    assert gateway.client.runs.last_event_id == "event-0"

    gateway.remote_runs[thread["id"]][run["id"]]["status"] = "success"
    gateway.states[thread["id"]]["interrupts"] = [{"value": {"question": "Continue?"}}]
    interrupted = client.get(
        f"/api/threads/{thread['id']}/runs/{run['id']}",
        headers=_headers(),
    )
    assert interrupted.json()["status"] == "interrupted"
    resumed = client.post(
        f"/api/threads/{thread['id']}/runs/{run['id']}/resume",
        json={"value": "yes"},
        headers=_headers(),
    )
    resumed.raise_for_status()
    assert gateway.submitted[-1]["command"] == {"resume": "yes"}

    cancel = client.post(
        f"/api/threads/{thread['id']}/runs/{resumed.json()['id']}/cancel",
        headers=_headers(),
    )
    assert cancel.status_code == 204

    snapshot = client.get(
        f"/api/threads/{thread['id']}/snapshot",
        headers=_headers(),
    )
    snapshot.raise_for_status()
    assert [item["id"] for item in snapshot.json()["assets"]] == [asset.id]
    assert "artifacts" not in snapshot.json()


def test_upload_contract_has_no_custom_steering_routes() -> None:
    client, _, _ = _client()
    thread = _thread(client)
    checksum = hashlib.sha256(b"x").hexdigest()
    ticket = client.post(
        f"/api/projects/{thread['metadata']['project_id']}/assets/uploads",
        json={
            "filename": "input.txt",
            "media_type": "text/plain",
            "size_bytes": 1,
            "sha256": checksum,
        },
        headers=_headers(),
    )
    ticket.raise_for_status()
    payload = ticket.json()
    assert payload["bucket_name"] == "langalpha-assets"
    assert payload["signed_url"] == "https://storage.test/upload"

    completed = client.post(
        f"/api/assets/{payload['asset']['id']}/complete",
        json={"sha256": checksum},
        headers=_headers(),
    )
    completed.raise_for_status()
    assert completed.json()["status"] == "ready"

    assert all("guidance" not in path for path in client.app.openapi()["paths"])


def test_asset_view_serves_supported_formats_inline_and_rejects_unknown_binary() -> None:
    client, _, assets = _client()
    thread = _thread(client)
    project_id = thread["metadata"]["project_id"]
    markdown = assets.ingest_input(
        owner_id="00000000-0000-0000-0000-000000000001",
        project_id=project_id,
        filename="report.md",
        media_type="application/octet-stream",
        content=b"# Report\n\nPreview me.",
    )
    html = assets.ingest_input(
        owner_id="00000000-0000-0000-0000-000000000001",
        project_id=project_id,
        filename="chart.html",
        media_type="text/html",
        content=b"<h1>Chart</h1>",
    )
    archive = assets.ingest_input(
        owner_id="00000000-0000-0000-0000-000000000001",
        project_id=project_id,
        filename="bundle.zip",
        media_type="application/zip",
        content=b"PK",
    )

    markdown_response = client.get(
        f"/api/assets/{markdown.id}/view",
        headers=_headers(),
    )
    assert markdown_response.status_code == 200
    assert markdown_response.text.startswith("# Report")
    assert markdown_response.headers["content-type"].startswith("text/markdown")
    assert markdown_response.headers["content-disposition"] == 'inline; filename="report.md"'
    assert markdown_response.headers["x-content-type-options"] == "nosniff"

    html_response = client.get(f"/api/assets/{html.id}/view", headers=_headers())
    assert html_response.status_code == 200
    assert html_response.headers["content-security-policy"].startswith("sandbox")

    unsupported_response = client.get(
        f"/api/assets/{archive.id}/view",
        headers=_headers(),
    )
    assert unsupported_response.status_code == 415
