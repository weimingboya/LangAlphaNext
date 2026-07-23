from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest
from daytona import CreateSandboxFromSnapshotParams, Daytona, DaytonaConfig

from langalpha.agent.model import build_model
from langalpha.config import get_settings

pytestmark = pytest.mark.external


def _require_external_environment() -> None:
    if os.getenv("RUN_EXTERNAL_E2E") != "1":
        pytest.skip("set RUN_EXTERNAL_E2E=1 to run paid external-service gates")
    missing = [name for name in ("OPENAI_API_KEY", "DAYTONA_API_KEY") if not os.getenv(name)]
    if missing:
        pytest.fail(f"missing external test environment: {', '.join(missing)}")


@pytest.mark.asyncio
async def test_real_openai_harness_profile() -> None:
    _require_external_environment()
    get_settings.cache_clear()
    response = await build_model().ainvoke(
        "Reply with a single short sentence confirming that the model endpoint is reachable."
    )
    assert str(response.content).strip()
    assert response.response_metadata


def test_real_daytona_lifecycle_and_isolation() -> None:
    _require_external_environment()
    settings = get_settings()
    client = Daytona(
        DaytonaConfig(
            api_key=settings.require_daytona_key(),
            target=settings.daytona_target,
        )
    )
    marker = uuid4().hex
    payload = f"langalpha-external-smoke:{marker}\n".encode()
    expected_checksum = hashlib.sha256(payload).hexdigest()
    sandbox = None
    try:
        sandbox = client.create(
            CreateSandboxFromSnapshotParams(
                language="python",
                name=f"langalpha-external-{marker[:12]}",
                labels={"app": "langalpha-next-external", "smoke_id": marker},
                auto_stop_interval=5,
                auto_archive_interval=60,
                auto_delete_interval=-1,
                public=False,
                network_block_all=True,
            ),
            timeout=120,
        )
        assert sandbox.public is False
        assert sandbox.network_block_all is True
        assert sandbox.auto_stop_interval == 5
        assert sandbox.auto_archive_interval == 60
        assert sandbox.auto_delete_interval == -1

        work_dir = sandbox.get_work_dir().rstrip("/")
        assert work_dir.startswith("/")
        path = f"{work_dir}/langalpha-external-smoke.txt"
        sandbox.fs.upload_file(payload, path)
        execute = sandbox.process.exec(
            f"python -c \"from pathlib import Path; print(Path('{path}').read_text())\"",
            timeout=30,
        )
        assert execute.exit_code == 0
        assert marker in execute.result

        blocked = sandbox.process.exec(
            'python -c "import urllib.request; '
            "urllib.request.urlopen('https://example.com', timeout=5)\"",
            timeout=15,
        )
        assert blocked.exit_code != 0

        sandbox.stop(timeout=120)
        sandbox.start(timeout=180)
        assert hashlib.sha256(sandbox.fs.download_file(path)).hexdigest() == expected_checksum

        sandbox.stop(timeout=120)
        sandbox.archive(request_timeout=120)
        sandbox.start(timeout=240)
        assert hashlib.sha256(sandbox.fs.download_file(path)).hexdigest() == expected_checksum
    finally:
        if sandbox is not None:
            sandbox.delete(timeout=180, wait=True)
