from __future__ import annotations

import sqlite3

import pytest

from langalpha.server.repository import Repository


async def test_repository_contains_only_product_owned_tables(tmp_path) -> None:
    path = tmp_path / "product.db"
    repository = Repository(path)
    await repository.initialize()
    thread = await repository.create_thread(
        graph_thread_id="graph-thread",
        workspace_id="workspace",
        title="Research",
        project_id="project",
    )
    binding = await repository.bind_sandbox(thread.id, "sandbox")
    artifact = await repository.upsert_artifact(
        thread_id=thread.id,
        run_id="graph-run",
        name="report.md",
        sandbox_path="/workspace/artifacts/report.md",
        media_type="text/markdown",
        size_bytes=42,
        checksum="abc",
    )
    guidance = await repository.create_guidance(
        thread_id=thread.id,
        run_id="control-run",
        message="Focus on cash flow",
    )

    with sqlite3.connect(path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {
        "product_threads",
        "runtime_bindings",
        "artifacts",
        "guidance",
    }
    assert binding.sandbox_id == "sandbox"
    assert (await repository.get_artifact(artifact.id)) == artifact
    assert [item.id for item in await repository.claim_guidance("control-run")] == [guidance.id]
    await repository.return_guidance("control-run", [guidance.id])
    assert (await repository.list_guidance("control-run"))[0].status == "returned"


async def test_guidance_moves_to_successor_control_id(tmp_path) -> None:
    repository = Repository(tmp_path / "guidance.db")
    await repository.initialize()
    thread = await repository.create_thread(
        graph_thread_id="graph-thread",
        workspace_id="workspace",
        title="Guidance",
    )
    guidance = await repository.create_guidance(
        thread_id=thread.id,
        run_id="control-parent",
        message="Use revised assumptions",
    )
    await repository.transfer_open_guidance("control-parent", "control-successor")
    assert await repository.list_guidance("control-parent") == []
    claimed = await repository.claim_guidance("control-successor")
    assert [item.id for item in claimed] == [guidance.id]
    assert claimed[0].run_id == "control-successor"


async def test_repository_rejects_obsolete_runtime_projection_database(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE product_runs (id TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="fresh LANGALPHA_DATABASE_PATH"):
        await Repository(path).initialize()
