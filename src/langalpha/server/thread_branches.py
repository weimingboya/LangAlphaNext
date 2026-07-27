from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langalpha.domain.models import ThreadBranchOption, ThreadBranchState
from langalpha.server.agent_gateway import as_dict, state_messages

_INPUT_FILES_MARKER = "\n\nAvailable input files:\n"


class InvalidBranch(ValueError):
    pass


def _checkpoint(state: object) -> dict[str, Any]:
    value = as_dict(state).get("checkpoint")
    return value if isinstance(value, dict) else {}


def checkpoint_id(state: object) -> str | None:
    value = _checkpoint(state).get("checkpoint_id")
    return str(value) if value else None


def _parent_checkpoint_id(state: object) -> str | None:
    value = as_dict(state).get("parent_checkpoint")
    if not isinstance(value, dict):
        return None
    parent_id = value.get("checkpoint_id")
    return str(parent_id) if parent_id else None


def _created_at(state: object) -> datetime:
    value = as_dict(state).get("created_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def editable_content(content: object) -> str:
    if not isinstance(content, str):
        return ""
    return content.split(_INPUT_FILES_MARKER, 1)[0].rstrip()


def _latest_user_message(state: object) -> dict[str, Any] | None:
    return next(
        (message for message in reversed(state_messages(state)) if message.get("role") == "user"),
        None,
    )


def _state_index(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {state_id: state for state in history if (state_id := checkpoint_id(state)) is not None}


def _leaf_ids(history: list[dict[str, Any]]) -> set[str]:
    ids = set(_state_index(history))
    parent_ids = {
        parent_id for state in history if (parent_id := _parent_checkpoint_id(state)) is not None
    }
    return ids - parent_ids


def branch_chain(
    history: list[dict[str, Any]],
    selected_checkpoint_id: str,
) -> list[dict[str, Any]]:
    states = _state_index(history)
    selected = states.get(selected_checkpoint_id)
    if selected is None:
        raise InvalidBranch("branch checkpoint was not found")

    chain: list[dict[str, Any]] = []
    current: dict[str, Any] | None = selected
    seen: set[str] = set()
    while current is not None:
        current_id = checkpoint_id(current)
        if current_id is None or current_id in seen:
            break
        seen.add(current_id)
        chain.append(current)
        parent_id = _parent_checkpoint_id(current)
        current = states.get(parent_id) if parent_id else None
    chain.reverse()
    return chain


def require_branch_leaf(
    history: list[dict[str, Any]],
    selected_checkpoint_id: str,
) -> None:
    if selected_checkpoint_id not in _leaf_ids(history):
        raise InvalidBranch("only a current branch can be continued")


def branch_run_ids(
    history: list[dict[str, Any]],
    selected_checkpoint_id: str | None,
) -> set[str]:
    if selected_checkpoint_id is None:
        return set()
    result: set[str] = set()
    for state in branch_chain(history, selected_checkpoint_id):
        metadata = as_dict(state).get("metadata")
        if (
            isinstance(metadata, dict)
            and metadata.get("run_id")
            and metadata.get("source") != "input"
        ):
            result.add(str(metadata["run_id"]))
    return result


def branch_state(
    history: list[dict[str, Any]],
    selected_state: dict[str, Any],
) -> ThreadBranchState:
    selected_id = checkpoint_id(selected_state)
    if selected_id is None:
        return ThreadBranchState()

    states = _state_index(history)
    options: list[ThreadBranchOption] = []
    for leaf_id in _leaf_ids(history):
        leaf = states[leaf_id]
        latest_user = _latest_user_message(leaf)
        options.append(
            ThreadBranchOption(
                checkpoint_id=leaf_id,
                preview=editable_content(
                    latest_user.get("content") if latest_user is not None else ""
                ),
                created_at=_created_at(leaf),
            )
        )
    options.sort(key=lambda option: (option.created_at, option.checkpoint_id))
    current_index = next(
        (index for index, option in enumerate(options) if option.checkpoint_id == selected_id),
        0,
    )
    latest_user = _latest_user_message(selected_state)
    return ThreadBranchState(
        current_checkpoint_id=selected_id,
        current_index=current_index,
        options=options,
        can_edit_latest=bool(
            selected_id in _leaf_ids(history)
            and latest_user is not None
            and editable_content(latest_user.get("content"))
        ),
    )


def latest_user_edit(
    history: list[dict[str, Any]],
    selected_checkpoint_id: str,
    replacement: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    require_branch_leaf(history, selected_checkpoint_id)

    chain = branch_chain(history, selected_checkpoint_id)
    selected_message = _latest_user_message(chain[-1])
    if selected_message is None:
        raise InvalidBranch("this branch has no user message to edit")
    message_id = str(selected_message.get("id") or "")
    if not message_id:
        raise InvalidBranch("the latest user message has no stable ID")

    message_checkpoint = next(
        (
            state
            for state in chain
            if any(
                message.get("role") == "user" and str(message.get("id")) == message_id
                for message in state_messages(state)
            )
        ),
        None,
    )
    if message_checkpoint is None:
        raise InvalidBranch("the latest user message checkpoint was not found")

    original_content = selected_message.get("content")
    suffix = ""
    if isinstance(original_content, str) and _INPUT_FILES_MARKER in original_content:
        suffix = _INPUT_FILES_MARKER + original_content.split(_INPUT_FILES_MARKER, 1)[1]
    replacement_message = {
        "id": message_id,
        "role": "user",
        "content": replacement.rstrip() + suffix,
    }
    metadata = as_dict(message_checkpoint).get("metadata")
    source_run_id = (
        str(metadata.get("run_id")) if isinstance(metadata, dict) and metadata.get("run_id") else ""
    )
    return _checkpoint(message_checkpoint), replacement_message, source_run_id
