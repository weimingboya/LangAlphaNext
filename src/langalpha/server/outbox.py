from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from langalpha.server.repository import Repository

RedisFactory = Callable[[str], Any]


class RedisOutboxPublisher:
    """Publish durable DomainEvents to Redis with at-least-once semantics."""

    def __init__(
        self,
        repository: Repository,
        url: str,
        *,
        channel_prefix: str = "langalpha:events",
        poll_interval: float = 0.5,
        client_factory: RedisFactory | None = None,
    ) -> None:
        self.repository = repository
        self.url = url
        self.channel_prefix = channel_prefix.rstrip(":")
        self.poll_interval = max(0.05, poll_interval)
        self._client_factory = client_factory or (
            lambda value: redis.from_url(value, decode_responses=True)
        )
        self._client: Any | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="redis-outbox-publisher")

    async def publish_once(self) -> int:
        if self._client is None:
            self._client = self._client_factory(self.url)
        published = 0
        for event in await self.repository.list_pending_outbox():
            channel = f"{self.channel_prefix}:{event.thread_id}"
            await self.repository.mark_outbox_attempt(event.id)
            await self._client.publish(channel, event.model_dump_json())
            await self.repository.mark_outbox_published(event.id)
            published += 1
        return published

    async def _run(self) -> None:
        delay = self.poll_interval
        while True:
            try:
                published = await self.publish_once()
                delay = self.poll_interval
                if published == 0:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                raise
            except RedisError:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            close: Callable[[], Awaitable[None]] | None = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
            self._client = None
