from __future__ import annotations

import asyncio

from langalpha.server.repository import ActiveRunConflict, Repository, event_types


async def test_repository_thread_run_event_and_artifact_lifecycle(tmp_path) -> None:
    repository = Repository(tmp_path / "langalpha.db")
    await repository.initialize()

    thread = await repository.create_thread(
        graph_thread_id="graph-thread",
        workspace_id="workspace",
        title="Research",
        thread_id="product-thread",
        project_id="project",
        owner_id="owner",
    )
    run = await repository.create_run(
        thread_id=thread.id,
        graph_run_id="graph-run",
        run_id="product-run",
        turn_id="turn",
    )
    await repository.update_run(run.id, "running")
    event = await repository.append_event(
        thread_id=thread.id,
        run_id=run.id,
        event_type="run.started",
        payload={"value": 1},
        source_event_key="graph-run:started",
    )
    duplicate = await repository.append_event(
        thread_id=thread.id,
        run_id=run.id,
        event_type="run.started",
        payload={"value": 2},
        source_event_key="graph-run:started",
    )
    artifact = await repository.create_artifact(
        thread_id=thread.id,
        run_id=run.id,
        name="report.md",
        sandbox_path="/workspace/artifacts/report.md",
        media_type="text/markdown",
        size_bytes=42,
        checksum="abc",
    )
    updated_artifact = await repository.upsert_artifact(
        thread_id=thread.id,
        run_id=run.id,
        name="report.md",
        sandbox_path="/workspace/artifacts/report.md",
        media_type="text/markdown",
        size_bytes=84,
        checksum="def",
    )
    guidance = await repository.create_guidance(
        thread_id=thread.id,
        run_id=run.id,
        message="Focus on cash flow",
    )

    assert (await repository.get_thread(thread.id)).title == "Research"
    assert (await repository.get_run(run.id)).status == "running"
    assert event_types(await repository.list_events(thread.id)) == ["run.started"]
    assert (await repository.get_artifact(artifact.id)).sandbox_path.endswith("report.md")
    assert event.sequence == 1
    assert duplicate.id == event.id
    assert await repository.pending_outbox_count() == 1
    assert updated_artifact.id == artifact.id
    assert updated_artifact.size_bytes == 84
    assert (await repository.get_binding(thread.id)).project_id == "project"
    binding = await repository.bind_sandbox(thread.id, "sandbox")
    assert binding.sandbox_id == "sandbox"

    claimed = await repository.claim_guidance(run.id)
    assert [item.id for item in claimed] == [guidance.id]
    assert claimed[0].status == "delivered"
    await repository.return_guidance(run.id, [guidance.id])
    returned = await repository.list_guidance(run.id)
    assert returned[0].status == "returned"


async def test_successor_run_claims_reclaimed_ancestor_guidance(tmp_path) -> None:
    repository = Repository(tmp_path / "guidance.db")
    await repository.initialize()
    thread = await repository.create_thread(
        graph_thread_id="graph-thread",
        workspace_id="workspace",
        title="Guidance recovery",
    )
    parent = await repository.create_run(
        thread_id=thread.id,
        graph_run_id="parent-runtime",
        run_id="parent",
        turn_id="turn",
        status="interrupted",
    )
    guidance = await repository.create_guidance(
        thread_id=thread.id,
        run_id=parent.id,
        message="Use the revised assumptions",
    )
    reclaimed = await repository.reclaim_guidance(parent.id)
    assert [item.id for item in reclaimed] == [guidance.id]
    successor = await repository.create_run(
        thread_id=thread.id,
        graph_run_id="successor-runtime",
        run_id="successor",
        turn_id=parent.turn_id,
        parent_run_id=parent.id,
        status="running",
    )

    claimed = await repository.claim_guidance(successor.id)
    assert [item.id for item in claimed] == [guidance.id]
    assert claimed[0].run_id == successor.id
    assert claimed[0].status == "delivered"
    assert await repository.list_guidance(parent.id) == []


async def test_repository_serializes_active_run_creation_per_thread(tmp_path) -> None:
    repository = Repository(tmp_path / "concurrency.db")
    await repository.initialize()
    thread = await repository.create_thread(
        graph_thread_id="runtime-thread",
        workspace_id="workspace",
        title="Concurrent run",
    )

    results = await asyncio.gather(
        repository.create_run(thread_id=thread.id, run_id="run-a"),
        repository.create_run(thread_id=thread.id, run_id="run-b"),
        return_exceptions=True,
    )

    assert sum(isinstance(result, ActiveRunConflict) for result in results) == 1
    created = [result for result in results if not isinstance(result, BaseException)]
    assert len(created) == 1
    assert created[0].status == "pending"
