import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from langalpha.backends.daytona import delete_daytona_sandbox
from langalpha.domain.models import ProjectCreate, ProjectPatch, ProjectView
from langalpha.server.async_task_lifecycle import cancel_child_tasks
from langalpha.server.dependencies import ServicesDep, UserDep

router = APIRouter(prefix="/api/projects")


@router.get("", response_model=list[ProjectView])
async def list_projects(user: UserDep, services: ServicesDep) -> list[ProjectView]:
    try:
        return await asyncio.to_thread(
            services.project_store.list_projects,
            owner_id=user.id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="project storage is unavailable") from exc


@router.post("", response_model=ProjectView, status_code=201)
async def create_project(body: ProjectCreate, user: UserDep, services: ServicesDep) -> ProjectView:
    try:
        return await asyncio.to_thread(
            services.project_store.create_project,
            owner_id=user.id,
            name=body.name,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="project could not be created") from exc


@router.get("/{project_id}", response_model=ProjectView)
async def get_project(project_id: str, user: UserDep, services: ServicesDep) -> ProjectView:
    return await services.require_project(project_id, user)


@router.patch("/{project_id}", response_model=ProjectView)
async def rename_project(
    project_id: str,
    body: ProjectPatch,
    user: UserDep,
    services: ServicesDep,
) -> ProjectView:
    await services.require_project(project_id, user)
    try:
        return await asyncio.to_thread(
            services.project_store.rename_project,
            owner_id=user.id,
            project_id=project_id,
            name=body.name,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="project could not be renamed") from exc


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, user: UserDep, services: ServicesDep) -> Response:
    project = await services.require_project(project_id, user, allow_deleting=True)
    try:
        if project.status == "active":
            await asyncio.to_thread(
                services.project_store.mark_deleting,
                owner_id=user.id,
                project_id=project.id,
            )
        threads = await services.gateway.search_threads(
            metadata={
                "app_id": services.settings.app_id,
                "owner_id": user.id,
                "project_id": project.id,
                "thread_kind": "main",
            },
            limit=1_000,
        )
        for thread in threads:
            runs = await services.gateway.runs(thread.id)
            for run in runs:
                if run.status in {"pending", "running"}:
                    await services.gateway.cancel(thread.id, run.id)
            await cancel_child_tasks(
                services.gateway,
                project_id=project.id,
                owner_id=user.id,
                parent_thread_id=thread.id,
                delete_threads=True,
            )
            await services.gateway.delete_thread(thread.id)
        project_assets = await asyncio.to_thread(
            services.asset_store.list_assets,
            owner_id=user.id,
            project_id=project.id,
        )
        for asset in project_assets:
            await asyncio.to_thread(
                services.asset_store.delete_asset,
                owner_id=user.id,
                asset_id=asset.id,
            )
        if project.sandbox_id:
            await asyncio.to_thread(
                delete_daytona_sandbox,
                sandbox_id=project.sandbox_id,
                owner_id=user.id,
                project_id=project.id,
            )
        await asyncio.to_thread(
            services.project_store.delete_project,
            owner_id=user.id,
            project_id=project.id,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="project deletion did not complete") from exc
    return Response(status_code=204)
