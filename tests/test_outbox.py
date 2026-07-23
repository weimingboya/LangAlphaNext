from __future__ import annotations

import json

import pytest
from redis.exceptions import RedisError

from langalpha.server.outbox import RedisOutboxPublisher
from langalpha.server.repository import Repository


class FakeRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.fail_second_once = True
        self.closed = False

    async def publish(self, channel: str, payload: str) -> None:
        if len(self.messages) == 1 and self.fail_second_once:
            self.fail_second_once = False
            raise RedisError("temporarily unavailable")
        self.messages.append((channel, payload))

    async def aclose(self) -> None:
        self.closed = True


async def test_redis_outbox_recovers_without_losing_unpublished_events(
    tmp_path,
) -> None:
    repository = Repository(tmp_path / "events.db")
    await repository.initialize()
    thread = await repository.create_thread(
        graph_thread_id="runtime-thread",
        workspace_id="workspace",
        title="Outbox",
    )
    first = await repository.append_event(
        thread_id=thread.id,
        run_id=None,
        event_type="test.first",
        payload={"value": 1},
        source_event_key="first",
    )
    second = await repository.append_event(
        thread_id=thread.id,
        run_id=None,
        event_type="test.second",
        payload={"value": 2},
        source_event_key="second",
    )
    client = FakeRedis()
    publisher = RedisOutboxPublisher(
        repository,
        "redis://unused",
        client_factory=lambda _: client,
    )

    with pytest.raises(RedisError):
        await publisher.publish_once()
    assert await repository.pending_outbox_count() == 1

    assert await publisher.publish_once() == 1
    assert await repository.pending_outbox_count() == 0
    assert [json.loads(payload)["id"] for _, payload in client.messages] == [
        first.id,
        second.id,
    ]
    assert all(channel == f"langalpha:events:{thread.id}" for channel, _ in client.messages)

    await publisher.close()
    assert client.closed is True
