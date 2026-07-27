from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from langalpha.domain.models import ThreadCreate, ThreadPatch, ThreadView
from langalpha.server.async_task_lifecycle import cancel_child_tasks
from langalpha.server.dependencies import ServicesDep, UserDep, remote_status

router = APIRouter()
_THREAD_SCHEMA_VERSION = 2


@router.post("/api/projects/{project_id}/threads", response_model=ThreadView, status_code=201)
async def create_thread(
    project_id: str,
    body: ThreadCreate,
    user: UserDep,
    services: ServicesDep,
) -> ThreadView:
    project = await services.require_project(project_id, user)
    metadata = {
        "schema_version": _THREAD_SCHEMA_VERSION,
        "app_id": services.settings.app_id,
        "project_id": project.id,
        "owner_id": user.id,
        "thread_kind": "main",
        "title": body.title,
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


@router.get("/api/projects/{project_id}/threads", response_model=list[ThreadView])
async def list_threads(project_id: str, user: UserDep, services: ServicesDep) -> list[ThreadView]:
    project = await services.require_project(project_id, user)
    try:
        threads = await services.gateway.search_threads(
            metadata={
                "app_id": services.settings.app_id,
                "project_id": project.id,
                "owner_id": user.id,
            },
            limit=1_000,
        )
        return [
            thread for thread in threads if thread.metadata.get("thread_kind") != "async_subagent"
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Agent Server could not list threads",
        ) from exc


@router.get("/api/threads/{thread_id}", response_model=ThreadView)
async def get_thread(
    thread_id: str,
    user: UserDep,
    services: ServicesDep,
) -> ThreadView:
    thread = await services.require_thread(thread_id, user)
    await services.project_for_thread(thread, user)
    return thread


@router.patch("/api/threads/{thread_id}", response_model=ThreadView)
async def update_thread(
    thread_id: str,
    body: ThreadPatch,
    user: UserDep,
    services: ServicesDep,
) -> ThreadView:
    thread = await services.require_thread(thread_id, user)
    await services.project_for_thread(thread, user)
    try:
        return await services.gateway.update_thread_metadata(thread.id, {"title": body.title})
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="Agent Server could not update the thread",
        ) from exc


@router.delete("/api/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    user: UserDep,
    services: ServicesDep,
) -> Response:
    thread = await services.require_thread(thread_id, user)
    await services.project_for_thread(thread, user)
    try:
        runs = await services.gateway.runs(thread.id)
        for run in runs:
            if run.status in {"pending", "running"}:
                await services.gateway.cancel(thread.id, run.id)
        await cancel_child_tasks(
            services.gateway,
            project_id=str(thread.metadata["project_id"]),
            owner_id=user.id,
            parent_thread_id=thread.id,
            delete_threads=True,
        )
        await services.gateway.delete_thread(thread.id)
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="thread deletion did not complete",
        ) from exc
    return Response(status_code=204)
