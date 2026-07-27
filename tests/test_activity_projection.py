from langalpha.domain.models import AgentEvent
from langalpha.server.activity_projection import project_activity_events
from langalpha.server.routes.runs import event_frame


def source_event(
    *,
    event_id: str = "event-1",
    event_type: str = "message.delta",
    payload: dict,
) -> AgentEvent:
    return AgentEvent(
        id=event_id,
        thread_id="thread-1",
        run_id="run-1",
        type=event_type,
        payload=payload,
    )


def test_projects_reasoning_summary_and_tool_call() -> None:
    source = source_event(
        payload={
            "value": [
                {
                    "type": "AIMessageChunk",
                    "id": "message-1",
                    "content": [
                        {
                            "type": "reasoning",
                            "id": "reasoning-1",
                            "summary": [{"type": "summary_text", "text": "Computing YoY growth"}],
                        },
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "sec_resolve_company",
                            "arguments": '{"query":"Apple"}',
                        },
                    ],
                }
            ]
        }
    )

    projected = project_activity_events(source)

    assert [event.type for event in projected] == ["activity.updated", "activity.updated"]
    assert projected[0].payload == {
        "id": "reasoning:run-1:reasoning-1",
        "kind": "reasoning",
        "title": "Analysis",
        "detail": "Computing YoY growth",
        "status": "running",
    }
    assert projected[1].payload == {
        "id": "tool:run-1:call-1",
        "kind": "tool",
        "title": "Resolve SEC company",
        "detail": "Apple",
        "status": "running",
        "tool_name": "sec_resolve_company",
    }


def test_projects_high_value_tool_result_summary() -> None:
    source = source_event(
        event_id="event-2",
        event_type="message.completed",
        payload={
            "value": [
                {
                    "type": "tool",
                    "name": "sec_list_filings",
                    "tool_call_id": "call-1",
                    "status": "success",
                    "content": '{"filings":[{},{}]}',
                }
            ]
        },
    )

    [projected] = project_activity_events(source)

    assert projected.payload["id"] == "tool:run-1:call-1"
    assert projected.payload["status"] == "complete"
    assert projected.payload["detail"] == "2 filings"


def test_ignores_encrypted_reasoning_without_summary() -> None:
    source = source_event(
        payload={
            "value": [
                {
                    "type": "AIMessageChunk",
                    "id": "message-1",
                    "content": [
                        {
                            "type": "reasoning",
                            "id": "reasoning-1",
                            "encrypted_content": "opaque",
                        }
                    ],
                }
            ]
        }
    )

    assert project_activity_events(source) == []


def test_subagent_result_replaces_launch_row_with_task_identity() -> None:
    launch = source_event(
        payload={
            "value": [
                {
                    "type": "AIMessageChunk",
                    "tool_calls": [
                        {
                            "id": "call-start",
                            "name": "start_async_task",
                            "args": {
                                "subagent_type": "researcher",
                                "description": "Check Apple revenue",
                            },
                        }
                    ],
                }
            ]
        }
    )
    result = source_event(
        event_id="event-2",
        event_type="message.completed",
        payload={
            "value": [
                {
                    "type": "tool",
                    "name": "start_async_task",
                    "tool_call_id": "call-start",
                    "content": "Launched async subagent. task_id: child-thread",
                }
            ]
        },
    )

    [launch_activity] = project_activity_events(launch)
    [result_activity] = project_activity_events(result)

    assert launch_activity.payload["id"] == "subagent:run-1:call-start"
    assert result_activity.payload["id"] == "subagent:run-1:child-thread"
    assert result_activity.payload["replaces_id"] == "subagent:run-1:call-start"


def test_derived_sse_frame_does_not_replace_upstream_cursor() -> None:
    source = source_event(payload={})

    assert event_frame(source).startswith(b"id: event-1\n")
    assert event_frame(source, include_cursor=False).startswith(b"event: message.delta\n")
