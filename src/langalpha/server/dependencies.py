import asyncio
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from langalpha.assets.store import (
    AssetNotFoundError,
    AssetStore,
    AssetValidationError,
    SupabaseAssetStore,
)
from langalpha.config import Settings
from langalpha.domain.models import Asset, ThreadView
from langalpha.server.agent_gateway import AgentGateway
from langalpha.server.auth import (
    AuthenticationError,
    Authenticator,
    AuthUser,
    SupabaseAuthenticator,
)


def remote_status(exc: Exception, *, not_found: int = 404) -> int:
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    if status == 404:
        return not_found
    return 409 if status == 409 else 502


def asset_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AssetNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AssetValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=502, detail="asset storage is unavailable")


class AppServices:
    def __init__(
        self,
        settings: Settings,
        gateway: AgentGateway,
        *,
        authenticator: Authenticator | None = None,
        asset_store: AssetStore | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self._authenticator = authenticator
        self._asset_store = asset_store

    @property
    def authenticator(self) -> Authenticator:
        if self._authenticator is None:
            self._authenticator = SupabaseAuthenticator(self.settings)
        return self._authenticator

    @property
    def asset_store(self) -> AssetStore:
        if self._asset_store is None:
            self._asset_store = SupabaseAssetStore(self.settings)
        return self._asset_store

    async def require_thread(self, thread_id: str, user: AuthUser) -> ThreadView:
        try:
            thread = await self.gateway.get_thread(thread_id)
        except Exception as exc:
            status = remote_status(exc)
            raise HTTPException(
                status_code=status,
                detail="thread not found" if status == 404 else "Agent Server is unavailable",
            ) from exc
        if (
            thread.metadata.get("owner_id") != user.id
            or thread.metadata.get("project_id") != self.settings.app_project_id
            or thread.metadata.get("thread_kind") == "async_subagent"
        ):
            raise HTTPException(status_code=404, detail="thread not found")
        return thread

    async def require_assets(
        self,
        *,
        user: AuthUser,
        thread: ThreadView,
        asset_ids: list[str],
    ) -> list[Asset]:
        try:
            return await asyncio.to_thread(
                self.asset_store.require_ready_inputs,
                owner_id=user.id,
                thread_id=thread.id,
                asset_ids=asset_ids,
            )
        except Exception as exc:
            raise asset_http_error(exc) from exc

    def run_context(
        self,
        *,
        user: AuthUser,
        thread: ThreadView,
        turn_id: str,
        input_asset_ids: list[str] | None = None,
    ) -> dict[str, object]:
        sandbox_id = thread.metadata.get("sandbox_id")
        return {
            "project_id": self.settings.app_project_id,
            "owner_id": user.id,
            "thread_id": thread.id,
            "turn_id": turn_id,
            "input_asset_ids": input_asset_ids or [],
            "expected_sandbox_id": sandbox_id if isinstance(sandbox_id, str) else None,
        }


def get_services(request: Request) -> AppServices:
    return request.app.state.services


async def current_user(
    request: Request,
    services: Annotated[AppServices, Depends(get_services)],
    authorization: Annotated[str | None, Header()] = None,
) -> AuthUser:
    token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    if not token:
        token = request.cookies.get("langalpha_access_token")
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        return await asyncio.to_thread(services.authenticator.authenticate, token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


ServicesDep = Annotated[AppServices, Depends(get_services)]
UserDep = Annotated[AuthUser, Depends(current_user)]
