from __future__ import annotations

from typing import Annotated, Any, NotRequired

from deepagents import DeepAgentState


def merge_ui_events(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {event["id"]: event for event in left}
    for event in right:
        by_id[event["id"]] = event
    return list(by_id.values())


class LangAlphaAgentState(DeepAgentState):
    ui: NotRequired[Annotated[list[dict[str, Any]], merge_ui_events]]
    turn_context: NotRequired[dict[str, Any]]
