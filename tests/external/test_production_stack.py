from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.external

_REQUIRED_ENV = (
    "OPENAI_API_KEY",
    "DAYTONA_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_TEST_ACCESS_TOKEN",
)


def _require_external_environment() -> None:
    if os.getenv("RUN_EXTERNAL_E2E") != "1":
        pytest.skip("set RUN_EXTERNAL_E2E=1 to run paid external-service gates")
    missing = [name for name in _REQUIRED_ENV if not os.getenv(name)]
    if missing:
        pytest.fail(f"missing external test environment: {', '.join(missing)}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(url: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"local service exited before becoming ready: {url}")
        try:
            if httpx.get(url, timeout=1).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    pytest.fail(f"local service did not become ready: {url}")


@contextmanager
def _local_stack(tmp_path: Path) -> Iterator[tuple[str, str]]:
    agent_port = _free_port()
    api_port = _free_port()
    agent_url = f"http://127.0.0.1:{agent_port}"
    api_url = f"http://127.0.0.1:{api_port}"
    project_id = f"langalpha-external-{uuid4().hex[:12]}"
    environment = {
        **os.environ,
        "LANGGRAPH_SERVER_URL": agent_url,
        "APP_PROJECT_ID": project_id,
        "APP_ENVIRONMENT": "external-test",
        "APP_VERSION": "external-test",
        "LANGSMITH_PROJECT": project_id,
        "LANGSMITH_TRACING": ("true" if os.getenv("LANGSMITH_API_KEY") else "false"),
        "LANGCHAIN_CALLBACKS_BACKGROUND": "false",
    }
    executable_dir = Path(sys.executable).parent
    agent_log = (tmp_path / "agent-server.log").open("wb")
    api_log = (tmp_path / "api.log").open("wb")
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
        _wait_for(f"{agent_url}/ok", agent, 45)
        _wait_for(f"{api_url}/health", api, 30)
        yield api_url, project_id
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


def _consume_stream(
    client: httpx.Client,
    thread_id: str,
    run_id: str,
) -> list[str]:
    event_types: list[str] = []
    with client.stream(
        "GET",
        f"/api/threads/{thread_id}/runs/{run_id}/stream",
        timeout=1_200,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("event: "):
                event_types.append(line.removeprefix("event: "))
    return event_types


def test_real_production_boundary(tmp_path: Path) -> None:
    _require_external_environment()
    headers = {"Authorization": f"Bearer {os.environ['SUPABASE_TEST_ACCESS_TOKEN']}"}
    with _local_stack(tmp_path) as (api_url, _project_id):
        with httpx.Client(base_url=api_url, headers=headers, timeout=60) as client:
            thread_response = client.post(
                "/api/threads",
                json={"title": "External production boundary"},
            )
            thread_response.raise_for_status()
            thread = thread_response.json()
            try:
                content = b"symbol\nAAPL\nMSFT\n"
                checksum = hashlib.sha256(content).hexdigest()
                ticket_response = client.post(
                    f"/api/threads/{thread['id']}/assets/uploads",
                    json={
                        "filename": "stocks.csv",
                        "media_type": "text/csv",
                        "size_bytes": len(content),
                        "sha256": checksum,
                    },
                )
                ticket_response.raise_for_status()
                ticket = ticket_response.json()
                upload = httpx.put(
                    ticket["signed_url"],
                    data={"cacheControl": "3600"},
                    files={"file": ("stocks.csv", content, "text/csv")},
                    timeout=180,
                )
                upload.raise_for_status()
                complete = client.post(
                    f"/api/assets/{ticket['asset']['id']}/complete",
                    json={"sha256": checksum},
                )
                complete.raise_for_status()

                run_response = client.post(
                    f"/api/threads/{thread['id']}/runs",
                    json={
                        "message": (
                            "Read the supplied CSV, summarize its symbols, and write "
                            "/workspace/artifacts/report.md. Finish without asking "
                            "for approval."
                        ),
                        "input_asset_ids": [ticket["asset"]["id"]],
                    },
                )
                run_response.raise_for_status()
                run = run_response.json()
                event_types = _consume_stream(client, thread["id"], run["id"])

                snapshot_response = client.get(f"/api/threads/{thread['id']}/snapshot")
                snapshot_response.raise_for_status()
                snapshot = snapshot_response.json()
                report = next(
                    asset
                    for asset in snapshot["assets"]
                    if asset["sandbox_path"] == "/workspace/artifacts/report.md"
                )
                download_ticket = client.post(f"/api/assets/{report['id']}/download-url").json()
                downloaded = httpx.get(download_ticket["url"], timeout=180)
                downloaded.raise_for_status()

                assert b"AAPL" in downloaded.content
                assert b"MSFT" in downloaded.content
                assert "sandbox.bound" in event_types
                assert "asset.ready" in event_types
                assert "run.success" in event_types
                assert snapshot["thread"]["metadata"]["sandbox_id"]
            finally:
                delete = client.delete(f"/api/threads/{thread['id']}", timeout=300)
                delete.raise_for_status()
