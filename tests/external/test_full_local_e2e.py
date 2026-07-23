from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from daytona import Daytona, DaytonaConfig, ListSandboxesQuery

pytestmark = pytest.mark.external


def _require_external_environment() -> None:
    if os.getenv("RUN_EXTERNAL_E2E") != "1":
        pytest.skip("set RUN_EXTERNAL_E2E=1 to run paid external-service gates")
    missing = [name for name in ("OPENAI_API_KEY", "DAYTONA_API_KEY") if not os.getenv(name)]
    if missing:
        pytest.fail(f"missing external test environment: {', '.join(missing)}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(url: str, process: subprocess.Popen[bytes], timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"local service exited before becoming ready: {url}")
        try:
            response = httpx.get(url, timeout=1)
            if response.is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    pytest.fail(f"local service did not become ready: {url}")


@contextmanager
def _local_stack(tmp_path: Path) -> Iterator[tuple[str, Path, str]]:
    agent_port = _free_port()
    api_port = _free_port()
    agent_url = f"http://127.0.0.1:{agent_port}"
    api_url = f"http://127.0.0.1:{api_port}"
    project_id = f"langalpha-external-{uuid4().hex[:12]}"
    database = tmp_path / "e2e.db"
    environment = {
        **os.environ,
        "LANGGRAPH_SERVER_URL": agent_url,
        "LANGALPHA_API_URL": api_url,
        "LANGALPHA_DATABASE_PATH": str(database),
        "LANGALPHA_PROJECT_ID": project_id,
        "LANGALPHA_OWNER_ID": "external-e2e",
        "LANGSMITH_PROJECT": project_id,
        "LANGSMITH_TRACING": ("true" if os.getenv("LANGSMITH_API_KEY") else "false"),
        "LANGCHAIN_CALLBACKS_BACKGROUND": "false",
    }
    executable_dir = Path(sys.executable).parent
    agent_log = (tmp_path / "agent-server.log").open("wb")
    api_log = (tmp_path / "control-plane.log").open("wb")
    agent = subprocess.Popen(
        [
            str(executable_dir / "langgraph"),
            "dev",
            "--no-browser",
            "--no-reload",
            "--port",
            str(agent_port),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=agent_log,
        stderr=subprocess.STDOUT,
    )
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "langalpha.server.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=api_log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for(f"{agent_url}/ok", agent, timeout=45)
        _wait_for(f"{api_url}/health", api, timeout=30)
        yield api_url, database, project_id
    finally:
        for process in (api, agent):
            process.terminate()
        for process in (api, agent):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        agent_log.close()
        api_log.close()


def _cleanup_daytona(project_id: str, workspace_id: str) -> None:
    client = Daytona(
        DaytonaConfig(
            api_key=os.environ["DAYTONA_API_KEY"],
            target=os.getenv("DAYTONA_TARGET", "us"),
        )
    )
    labels = {
        "app": "langalpha-next",
        "workspace_id": workspace_id,
        "project_id": project_id,
    }
    for sandbox in client.list(ListSandboxesQuery(labels=labels, limit=10)):
        sandbox.delete(timeout=180, wait=True)


def _wait_for_run(
    client: httpx.Client,
    run_id: str,
    *,
    timeout: float = 1_200,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        current = response.json()
        if current["status"] in {
            "success",
            "error",
            "interrupted",
            "cancelled",
        }:
            return current
        time.sleep(1)
    pytest.fail(f"run did not reach a terminal or interrupted state: {run_id}")


def _assert_no_credentials_persisted(root: Path) -> None:
    secrets = [
        os.environ[name].encode()
        for name in ("OPENAI_API_KEY", "DAYTONA_API_KEY", "LANGSMITH_API_KEY")
        if os.getenv(name)
    ]
    for path in root.iterdir():
        if not path.is_file():
            continue
        content = path.read_bytes()
        assert all(secret not in content for secret in secrets), path.name


def _wait_for_langsmith_trace(project_id: str, timeout: float = 90) -> None:
    executable = shutil.which("langsmith")
    if executable is None:
        pytest.fail("langsmith CLI is required for the external trace gate")
    deadline = time.monotonic() + timeout
    environment = {**os.environ, "LANGSMITH_PROJECT": project_id}
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                executable,
                "trace",
                "list",
                "--project",
                project_id,
                "--last-n-minutes",
                "10",
                "--limit",
                "10",
                "--show-hierarchy",
                "--include-metadata",
                "--format",
                "json",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                payload = None
            if payload and ("trace_id" in result.stdout or "traces" in result.stdout):
                return
        time.sleep(2)
    pytest.fail(f"LangSmith trace was not queryable for project: {project_id}")


def test_real_local_vertical_slice(tmp_path) -> None:
    _require_external_environment()
    with _local_stack(tmp_path) as (api_url, database, project_id):
        workspace_id = ""
        try:
            with httpx.Client(base_url=api_url, timeout=30) as client:
                thread_response = client.post(
                    "/api/threads",
                    json={"title": "External golden research"},
                )
                thread_response.raise_for_status()
                thread = thread_response.json()
                workspace_id = thread["workspace_id"]

                upload = client.post(
                    f"/api/threads/{thread['id']}/files",
                    files={
                        "file": (
                            "stocks.csv",
                            b"symbol\nAAPL\nMSFT\n",
                            "text/csv",
                        )
                    },
                    timeout=180,
                )
                upload.raise_for_status()

                request = client.post(
                    f"/api/threads/{thread['id']}/runs",
                    json={
                        "message": (
                            "Read /workspace/uploads to find stocks.csv. Use "
                            "get_market_quotes for every symbol, materialize the "
                            "records, and use Daytona Python through execute to "
                            "calculate percentage changes. Start an async researcher "
                            "task to verify the calculation independently, check it "
                            "until complete, and incorporate its result. Write the "
                            "final report to /workspace/artifacts/report.md and call "
                            "show_widget with the calculated values. Finish without "
                            "asking for plan approval."
                        )
                    },
                )
                request.raise_for_status()
                run = request.json()
                current = _wait_for_run(client, run["id"])
                assert current["status"] == "success"

                runtime_state = client.get(f"/internal/threads/{thread['id']}/state")
                runtime_state.raise_for_status()
                state_text = runtime_state.text
                assert "start_async_task" in state_text
                assert "check_async_task" in state_text

                artifacts = client.get(f"/api/threads/{thread['id']}/artifacts").json()
                report = next(
                    artifact
                    for artifact in artifacts
                    if artifact["sandbox_path"] == "/workspace/artifacts/report.md"
                )
                downloaded = client.get(f"/api/artifacts/{report['id']}", timeout=180)
                downloaded.raise_for_status()
                assert b"AAPL" in downloaded.content
                assert b"MSFT" in downloaded.content

            with sqlite3.connect(database) as db:
                event_types = [
                    row[0]
                    for row in db.execute(
                        "SELECT type FROM domain_events WHERE thread_id = ? ORDER BY sequence",
                        (thread["id"],),
                    )
                ]
                duplicate_events = db.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT source_event_key, type, COUNT(*) AS count
                        FROM domain_events
                        WHERE thread_id = ?
                        GROUP BY source_event_key, type
                        HAVING count > 1
                    )
                    """,
                    (thread["id"],),
                ).fetchone()[0]
            assert "sandbox.bound" in event_types
            assert "artifact.updated" in event_types
            assert "widget.ready" in event_types
            assert "usage.updated" in event_types
            assert "run.success" in event_types
            assert duplicate_events == 0
        finally:
            if workspace_id:
                _cleanup_daytona(project_id, workspace_id)
    if os.getenv("LANGSMITH_API_KEY"):
        _wait_for_langsmith_trace(project_id)
    _assert_no_credentials_persisted(tmp_path)


def test_real_local_hitl_interrupt_and_resume(tmp_path) -> None:
    _require_external_environment()
    with _local_stack(tmp_path) as (api_url, database, _project_id):
        with httpx.Client(base_url=api_url, timeout=30) as client:
            thread_response = client.post(
                "/api/threads",
                json={"title": "External HITL"},
            )
            thread_response.raise_for_status()
            thread = thread_response.json()
            request = client.post(
                f"/api/threads/{thread['id']}/runs",
                json={
                    "message": (
                        "Before answering, you must call ask_user exactly once to "
                        "ask which market the analysis should cover. After the "
                        "answer is resumed, acknowledge the selected market and "
                        "finish without calling ask_user again."
                    )
                },
            )
            request.raise_for_status()
            interrupted = _wait_for_run(client, request.json()["id"], timeout=300)
            assert interrupted["status"] == "interrupted"

            resumed_response = client.post(
                f"/api/runs/{interrupted['id']}/resume",
                json={"value": "US technology equities"},
            )
            resumed_response.raise_for_status()
            resumed = _wait_for_run(
                client,
                resumed_response.json()["id"],
                timeout=300,
            )
            assert resumed["status"] == "success"
            assert resumed["parent_run_id"] == interrupted["id"]
            assert resumed["turn_id"] == interrupted["turn_id"]

        with sqlite3.connect(database) as db:
            event_types = [
                row[0]
                for row in db.execute(
                    "SELECT type FROM domain_events WHERE thread_id = ? ORDER BY sequence",
                    (thread["id"],),
                )
            ]
        assert event_types.count("interrupt.requested") == 1
        assert event_types.count("interrupt.resumed") == 1
        assert event_types.count("run.interrupted") == 1
        assert event_types.count("run.success") == 1
    _assert_no_credentials_persisted(tmp_path)
