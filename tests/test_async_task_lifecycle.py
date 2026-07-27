from types import SimpleNamespace

from langalpha.server.async_task_lifecycle import cancel_child_tasks


async def test_cancel_child_tasks_scopes_by_parent_and_deletes() -> None:
    calls: list[tuple[str, str]] = []

    class Gateway:
        async def search_threads(self, *, metadata, limit, offset):
            assert metadata == {
                "project_id": "project",
                "owner_id": "owner",
                "parent_thread_id": "parent",
                "thread_kind": "async_subagent",
                "parent_turn_id": "turn",
            }
            assert (limit, offset) == (100, 0)
            return [SimpleNamespace(id="child")]

        async def runs(self, thread_id):
            assert thread_id == "child"
            return [
                SimpleNamespace(id="run-active", status="running"),
                SimpleNamespace(id="run-done", status="success"),
            ]

        async def cancel(self, thread_id, run_id):
            calls.append(("cancel", f"{thread_id}:{run_id}"))

        async def delete_thread(self, thread_id):
            calls.append(("delete", thread_id))

    await cancel_child_tasks(
        Gateway(),  # type: ignore[arg-type]
        project_id="project",
        owner_id="owner",
        parent_thread_id="parent",
        parent_turn_id="turn",
        delete_threads=True,
    )

    assert calls == [
        ("cancel", "child:run-active"),
        ("delete", "child"),
    ]
