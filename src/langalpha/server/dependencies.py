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
from langalpha.domain.models import Asset, ProjectView, ThreadView
from langalpha.projects.store import (
    ProjectNotFoundError,
    ProjectStore,
    SupabaseProjectStore,
)
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
        project_store: ProjectStore | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self._authenticator = authenticator
        self._asset_store = asset_store
        self._project_store = project_store

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

    @property
    def project_store(self) -> ProjectStore:
        if self._project_store is None:
            self._project_store = SupabaseProjectStore(self.settings)
        return self._project_store

    async def require_project(
        self,
        project_id: str,
        user: AuthUser,
        *,
        allow_deleting: bool = False,
    ) -> ProjectView:
        try:
            project = await asyncio.to_thread(
                self.project_store.get_project,
                owner_id=user.id,
                project_id=project_id,
            )
        except (ProjectNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail="project not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="project storage is unavailable") from exc
        if project.status != "active" and not (allow_deleting and project.status == "deleting"):
            raise HTTPException(status_code=409, detail="project is not active")
        return project

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
            or thread.metadata.get("app_id") != self.settings.app_id
            or thread.metadata.get("thread_kind") == "async_subagent"
        ):
            raise HTTPException(status_code=404, detail="thread not found")
        return thread

    async def require_project_thread(
        self, project_id: str, thread_id: str, user: AuthUser
    ) -> tuple[ProjectView, ThreadView]:
        project, thread = await asyncio.gather(
            self.require_project(project_id, user),
            self.require_thread(thread_id, user),
        )
        if thread.metadata.get("project_id") != project.id:
            raise HTTPException(status_code=404, detail="thread not found")
        return project, thread

    async def project_for_thread(self, thread: ThreadView, user: AuthUser) -> ProjectView:
        project_id = thread.metadata.get("project_id")
        if not isinstance(project_id, str):
            raise HTTPException(status_code=404, detail="project not found")
        return await self.require_project(project_id, user)

    async def require_assets(
        self,
        *,
        user: AuthUser,
        project: ProjectView,
        thread: ThreadView,
        asset_ids: list[str],
    ) -> list[Asset]:
        try:
            return await asyncio.to_thread(
                self.asset_store.require_ready_inputs,
                owner_id=user.id,
                project_id=project.id,
                asset_ids=asset_ids,
            )
        except Exception as exc:
            raise asset_http_error(exc) from exc

    def run_context(
        self,
        *,
        user: AuthUser,
        project: ProjectView,
        thread: ThreadView,
        turn_id: str,
        input_asset_ids: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "app_id": self.settings.app_id,
            "project_id": project.id,
            "owner_id": user.id,
            "thread_id": thread.id,
            "turn_id": turn_id,
            "input_asset_ids": input_asset_ids or [],
            "expected_sandbox_id": project.sandbox_id,
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
