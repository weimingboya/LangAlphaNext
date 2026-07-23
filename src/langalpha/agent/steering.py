from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.config import get_stream_writer

from langalpha.agent.context import RunContext
from langalpha.config import get_settings


class TurnSteeringMiddleware(AgentMiddleware[Any, RunContext, Any]):
    """Deliver persisted same-run guidance immediately before a model call."""

    @staticmethod
    def _headers() -> dict[str, str]:
        token = get_settings().langalpha_internal_token
        if token is None:
            return {}
        return {"Authorization": f"Bearer {token.get_secret_value()}"}

    @staticmethod
    def _url(run_id: str, action: str) -> str:
        base = get_settings().langalpha_api_url.rstrip("/")
        return f"{base}/internal/runs/{run_id}/guidance/{action}"

    @staticmethod
    def _with_guidance(
        request: ModelRequest[RunContext],
        items: list[dict[str, Any]],
    ) -> ModelRequest[RunContext]:
        if not items:
            return request
        content = "\n\n".join(
            f"[用户运行中补充 #{item['id']}]\n{item['message']}" for item in items
        )
        message = HumanMessage(
            content=content,
            additional_kwargs={"langalpha_guidance_ids": [item["id"] for item in items]},
        )
        try:
            get_stream_writer()(
                {
                    "type": "steering.delivered",
                    "guidance_ids": [item["id"] for item in items],
                }
            )
        except RuntimeError:
            pass
        return request.override(messages=[*request.messages, message])

    @staticmethod
    def _items(response: httpx.Response) -> list[dict[str, Any]]:
        payload = response.json()
        if not isinstance(payload, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("message"), str)
            for item in payload
        ):
            raise ValueError("invalid guidance response")
        return payload

    def wrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        context = request.runtime.context
        if context is None:
            return handler(request)
        try:
            with httpx.Client(timeout=2) as client:
                response = client.post(
                    self._url(context.product_run_id, "claim"),
                    headers=self._headers(),
                )
                response.raise_for_status()
                items = self._items(response)
        except (httpx.HTTPError, TypeError, ValueError):
            return handler(request)

        result = handler(self._with_guidance(request, items))
        if items:
            try:
                with httpx.Client(timeout=2) as client:
                    client.post(
                        self._url(context.product_run_id, "return"),
                        headers=self._headers(),
                        json={"ids": [item["id"] for item in items]},
                    ).raise_for_status()
            except httpx.HTTPError:
                pass
        return result

    async def awrap_model_call(
        self,
        request: ModelRequest[RunContext],
        handler: Callable[[ModelRequest[RunContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        context = request.runtime.context
        if context is None:
            return await handler(request)
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.post(
                    self._url(context.product_run_id, "claim"),
                    headers=self._headers(),
                )
                response.raise_for_status()
                items = self._items(response)
        except (httpx.HTTPError, TypeError, ValueError):
            return await handler(request)

        result = await handler(self._with_guidance(request, items))
        if items:
            try:
                async with httpx.AsyncClient(timeout=2) as client:
                    returned = await client.post(
                        self._url(context.product_run_id, "return"),
                        headers=self._headers(),
                        json={"ids": [item["id"] for item in items]},
                    )
                    returned.raise_for_status()
            except httpx.HTTPError:
                pass
        return result
