import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from langalpha.backends.daytona import delete_daytona_sandbox
from langalpha.domain.models import ThreadCreate, ThreadPatch, ThreadView
from langalpha.server.async_task_lifecycle import cancel_child_tasks
from langalpha.server.dependencies import ServicesDep, UserDep, remote_status

router = APIRouter(prefix="/api/threads")
_THREAD_SCHEMA_VERSION = 1


@router.post("", response_model=ThreadView, status_code=201)
async def create_thread(
    body: ThreadCreate,
    user: UserDep,
    services: ServicesDep,
) -> ThreadView:
    metadata = {
        "schema_version": _THREAD_SCHEMA_VERSION,
        "project_id": services.settings.app_project_id,
        "owner_id": user.id,
        "thread_kind": "main",
        "title": body.title,
        "sandbox_id": None,
    }
    try:
        return await services.gateway.create_thread(
            thread_id=str(uuid4()),
            metadata=metadata,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="Agent Server could not create the thread",
        ) from exc


@router.get("", response_model=list[ThreadView])
async def list_threads(user: UserDep, services: ServicesDep) -> list[ThreadView]:
    try:
        threads = await services.gateway.search_threads(
            metadata={
                "project_id": services.settings.app_project_id,
                "owner_id": user.id,
            },
            limit=100,
        )
        return [
            thread for thread in threads if thread.metadata.get("thread_kind") != "async_subagent"
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Agent Server could not list threads",
        ) from exc


@router.get("/{thread_id}", response_model=ThreadView)
async def get_thread(
    thread_id: str,
    user: UserDep,
    services: ServicesDep,
) -> ThreadView:
    return await services.require_thread(thread_id, user)


@router.patch("/{thread_id}", response_model=ThreadView)
async def update_thread(
    thread_id: str,
    body: ThreadPatch,
    user: UserDep,
    services: ServicesDep,
) -> ThreadView:
    thread = await services.require_thread(thread_id, user)
    try:
        return await services.gateway.update_thread_metadata(thread.id, {"title": body.title})
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="Agent Server could not update the thread",
        ) from exc


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    user: UserDep,
    services: ServicesDep,
) -> Response:
    thread = await services.require_thread(thread_id, user)
    try:
        runs = await services.gateway.runs(thread.id)
        for run in runs:
            if run.status in {"pending", "running"}:
                await services.gateway.cancel(thread.id, run.id)
        await cancel_child_tasks(
            services.gateway,
            project_id=services.settings.app_project_id,
            owner_id=user.id,
            parent_thread_id=thread.id,
            delete_threads=True,
        )
        thread_assets = await asyncio.to_thread(
            services.asset_store.list_assets,
            owner_id=user.id,
            thread_id=thread.id,
        )
        for asset in thread_assets:
            await asyncio.to_thread(
                services.asset_store.delete_asset,
                owner_id=user.id,
                asset_id=asset.id,
            )
        sandbox_id = thread.metadata.get("sandbox_id")
        if isinstance(sandbox_id, str):
            await asyncio.to_thread(
                delete_daytona_sandbox,
                sandbox_id=sandbox_id,
                thread_id=thread.id,
                project_id=services.settings.app_project_id,
            )
        await services.gateway.delete_thread(thread.id)
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="thread deletion did not complete",
        ) from exc
    return Response(status_code=204)
