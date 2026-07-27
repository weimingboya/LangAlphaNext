from langalpha.server.thread_branches import (
    branch_run_ids,
    branch_state,
    latest_user_edit,
)


def _state(
    checkpoint_id: str,
    *,
    parent_id: str | None,
    messages: list[dict],
    run_id: str,
    source: str,
    created_at: str,
) -> dict:
    return {
        "values": {"messages": messages},
        "checkpoint": {
            "thread_id": "thread-1",
            "checkpoint_ns": "",
            "checkpoint_id": checkpoint_id,
        },
        "parent_checkpoint": (
            {
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": parent_id,
            }
            if parent_id
            else None
        ),
        "metadata": {"run_id": run_id, "source": source},
        "created_at": created_at,
        "interrupts": [],
    }


def test_latest_user_edit_reuses_message_id_and_preserves_the_original_branch() -> None:
    original = {"id": "user-1", "role": "user", "content": "Original question"}
    edited = {"id": "user-1", "role": "user", "content": "Edited question"}
    history = [
        _state(
            "final-edited",
            parent_id="fork-edited",
            messages=[edited, {"id": "answer-2", "role": "assistant", "content": "B"}],
            run_id="run-2",
            source="loop",
            created_at="2026-01-01T00:00:04Z",
        ),
        _state(
            "fork-edited",
            parent_id="input-original",
            messages=[edited],
            run_id="run-2",
            source="update",
            created_at="2026-01-01T00:00:03Z",
        ),
        _state(
            "final-original",
            parent_id="input-original",
            messages=[original, {"id": "answer-1", "role": "assistant", "content": "A"}],
            run_id="run-1",
            source="loop",
            created_at="2026-01-01T00:00:02Z",
        ),
        _state(
            "input-original",
            parent_id=None,
            messages=[original],
            run_id="run-1",
            source="input",
            created_at="2026-01-01T00:00:01Z",
        ),
    ]

    projection = branch_state(history, history[0])
    assert projection.current_index == 1
    assert [option.preview for option in projection.options] == [
        "Original question",
        "Edited question",
    ]
    assert projection.can_edit_latest is True
    assert branch_run_ids(history, "final-edited") == {"run-2"}

    checkpoint, replacement, source_run_id = latest_user_edit(
        history,
        "final-original",
        "A better question",
    )
    assert checkpoint["checkpoint_id"] == "input-original"
    assert replacement == {
        "id": "user-1",
        "role": "user",
        "content": "A better question",
    }
    assert source_run_id == "run-1"
