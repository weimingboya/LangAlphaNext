from langalpha.domain.models import AgentEvent
from langalpha.server.public_projection import project_public_events, public_messages


def test_public_projection_keeps_answer_and_safe_citations_only() -> None:
    source = AgentEvent(
        id="event-1",
        thread_id="thread-1",
        run_id="run-1",
        type="message.completed",
        payload={
            "value": [
                {
                    "type": "AIMessage",
                    "id": "message-1",
                    "content": [
                        {
                            "type": "reasoning",
                            "summary": [{"type": "summary_text", "text": "Private summary"}],
                            "encrypted_content": "opaque",
                        },
                        {
                            "type": "output_text",
                            "text": "Revenue increased.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.sec.gov/example",
                                    "title": "SEC",
                                    "start_index": 0,
                                    "end_index": 17,
                                },
                                {
                                    "type": "url_citation",
                                    "url": "javascript:alert(1)",
                                },
                            ],
                        },
                    ],
                    "tool_calls": [{"name": "sec_get_filing", "args": {"cik": "secret"}}],
                }
            ]
        },
    )

    [projected] = project_public_events(source)

    assert projected.payload == {
        "messages": [
            {
                "id": "message-1",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Revenue increased.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://www.sec.gov/example",
                                "title": "SEC",
                                "start_index": 0,
                                "end_index": 17,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    assert "opaque" not in str(projected.payload)
    assert "secret" not in str(projected.payload)


def test_public_projection_omits_tool_messages_and_reasoning_only_messages() -> None:
    messages = [
        {
            "id": "tool-1",
            "role": "tool",
            "name": "sec_get_filing",
            "content": '{"secret":"result"}',
        },
        {
            "id": "assistant-1",
            "role": "assistant",
            "content": [{"type": "reasoning", "encrypted_content": "opaque"}],
        },
        {
            "id": "user-1",
            "role": "user",
            "content": "Research Apple",
        },
    ]

    assert public_messages(messages) == [
        {
            "id": "user-1",
            "role": "user",
            "content": "Research Apple",
        }
    ]
