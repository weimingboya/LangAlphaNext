from __future__ import annotations

from langalpha.server.agent_gateway import AgentGateway


async def child_threads(
    gateway: AgentGateway,
    *,
    project_id: str,
    owner_id: str,
    parent_thread_id: str,
    parent_turn_id: str | None = None,
) -> list[str]:
    metadata = {
        "project_id": project_id,
        "owner_id": owner_id,
        "parent_thread_id": parent_thread_id,
        "thread_kind": "async_subagent",
    }
    if parent_turn_id is not None:
        metadata["parent_turn_id"] = parent_turn_id

    result: list[str] = []
    offset = 0
    while True:
        page = await gateway.search_threads(metadata=metadata, limit=100, offset=offset)
        result.extend(thread.id for thread in page)
        if len(page) < 100:
            return result
        offset += len(page)


async def cancel_child_tasks(
    gateway: AgentGateway,
    *,
    project_id: str,
    owner_id: str,
    parent_thread_id: str,
    parent_turn_id: str | None = None,
    delete_threads: bool = False,
) -> None:
    for thread_id in await child_threads(
        gateway,
        project_id=project_id,
        owner_id=owner_id,
        parent_thread_id=parent_thread_id,
        parent_turn_id=parent_turn_id,
    ):
        for run in await gateway.runs(thread_id):
            if run.status in {"pending", "running"}:
                await gateway.cancel(thread_id, run.id)
        if delete_threads:
            await gateway.delete_thread(thread_id)
