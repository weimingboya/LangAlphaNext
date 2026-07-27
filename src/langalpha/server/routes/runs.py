import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from langalpha.assets.store import safe_filename
from langalpha.domain.models import AgentEvent, ResumeRun, RunCreate, RunView, ThreadSnapshot
from langalpha.server.activity_projection import project_activity_events
from langalpha.server.agent_gateway import (
    normalize_messages,
    normalize_stream_part,
    run_view,
    state_interrupts,
    state_messages,
    state_todos,
    state_widgets,
    summarize_usage,
)
from langalpha.server.async_task_lifecycle import cancel_child_tasks
from langalpha.server.dependencies import ServicesDep, UserDep, remote_status
from langalpha.server.public_projection import project_public_events
from langalpha.server.thread_branches import (
    InvalidBranch,
    branch_run_ids,
    branch_state,
    editable_content,
    latest_user_edit,
    require_branch_leaf,
)
from langalpha.server.thread_branches import (
    checkpoint_id as state_checkpoint_id,
)

router = APIRouter(prefix="/api/threads/{thread_id}")
logger = logging.getLogger(__name__)


def event_frame(event: AgentEvent, *, include_cursor: bool = True) -> bytes:
    cursor = f"id: {event.id}\n" if include_cursor else ""
    return f"{cursor}event: {event.type}\ndata: {event.model_dump_json()}\n\n".encode()


def snapshot_activities(
    *,
    thread_id: str,
    messages: list[dict],
    runs: list[RunView],
) -> list[AgentEvent]:
    turn_runs = list(reversed([run for run in runs if run.parent_run_id is None]))
    fallback_run_id = turn_runs[0].id if turn_runs else f"snapshot:{thread_id}"
    turn_index = -1
    current_run_id = fallback_run_id
    activities: list[AgentEvent] = []
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            turn_index += 1
            if turn_index < len(turn_runs):
                current_run_id = turn_runs[turn_index].id
            continue
        if message.get("role") not in {"assistant", "tool"}:
            continue
        source = AgentEvent(
            id=f"snapshot:message:{message.get('id') or index}",
            thread_id=thread_id,
            run_id=current_run_id,
            type="message.completed",
            payload=message,
        )
        activities.extend(project_activity_events(source))
    return activities


@router.post("/runs", response_model=RunView, status_code=202)
async def create_run(
    thread_id: str,
    body: RunCreate,
    user: UserDep,
    services: ServicesDep,
) -> RunView:
    thread = await services.require_thread(thread_id, user)
    project = await services.project_for_thread(thread, user)
    try:
        state = await services.gateway.state(
            thread.id,
            checkpoint_id=body.branch_checkpoint_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Agent Server state is unavailable",
        ) from exc
    if state_interrupts(state):
        raise HTTPException(
            status_code=409,
            detail="thread is waiting for input; resume its interrupted run",
        )

    history: list[dict] = []
    if body.branch_checkpoint_id:
        try:
            history = await services.gateway.history(thread.id)
            require_branch_leaf(history, body.branch_checkpoint_id)
        except InvalidBranch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Agent Server history is unavailable",
            ) from exc

    if body.edit_latest and not body.branch_checkpoint_id:
        raise HTTPException(
            status_code=422,
            detail="editing requires a selected branch",
        )
    if body.edit_latest and body.strategy != "enqueue":
        raise HTTPException(
            status_code=409,
            detail="a message cannot be edited while a run is active",
        )

    source_checkpoint: dict | None = None
    run_input: dict | None
    source_run_id = ""
    asset_ids = list(body.input_asset_ids)
    if body.edit_latest:
        try:
            source_checkpoint, replacement_message, source_run_id = latest_user_edit(
                history,
                body.branch_checkpoint_id or "",
                body.message,
            )
            if source_run_id:
                source_metadata = await services.gateway.run_metadata(
                    thread.id,
                    source_run_id,
                )
                raw_asset_ids = source_metadata.get("input_asset_ids")
                if isinstance(raw_asset_ids, list):
                    asset_ids = [str(asset_id) for asset_id in raw_asset_ids]
        except InvalidBranch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Agent Server could not prepare the edited branch",
            ) from exc
        run_input = None
    else:
        replacement_message = None
        run_input = {"messages": [{"role": "user", "content": body.message}]}

    input_assets = await services.require_assets(
        user=user,
        project=project,
        thread=thread,
        asset_ids=asset_ids,
    )
    if input_assets and not body.edit_latest:
        paths = [
            f"/workspace/input/assets/{asset.id}/{safe_filename(asset.filename)}"
            for asset in input_assets
        ]
        message = f"{body.message}\n\nAvailable input files:\n" + "\n".join(
            f"- {path}" for path in paths
        )
        run_input = {"messages": [{"role": "user", "content": message}]}

    turn_id = str(uuid4())
    settings = services.settings
    metadata = {
        "app_id": settings.app_id,
        "project_id": project.id,
        "owner_id": user.id,
        "thread_id": thread.id,
        "turn_id": turn_id,
        "app_version": settings.app_version,
        "environment": settings.app_environment,
        "input_asset_ids": asset_ids,
        "branch_parent_checkpoint_id": body.branch_checkpoint_id,
        "edit_latest": body.edit_latest,
    }
    try:
        run_checkpoint = (
            {
                "thread_id": thread.id,
                "checkpoint_ns": "",
                "checkpoint_id": body.branch_checkpoint_id,
            }
            if body.branch_checkpoint_id
            else None
        )
        if body.edit_latest:
            if source_checkpoint is None or replacement_message is None:
                raise InvalidBranch("the latest user message could not be edited")
            fork = await services.gateway.update_state(
                thread.id,
                checkpoint=source_checkpoint,
                values={"messages": [replacement_message]},
            )
            fork_checkpoint = fork.get("checkpoint")
            if not isinstance(fork_checkpoint, dict):
                raise RuntimeError("Agent Server did not return a fork checkpoint")
            run_checkpoint = fork_checkpoint
            metadata["edited_message_id"] = replacement_message["id"]
            metadata["source_run_id"] = source_run_id

        create_kwargs = {
            "input": run_input,
            "context": services.run_context(
                user=user,
                project=project,
                thread=thread,
                turn_id=turn_id,
                input_asset_ids=asset_ids,
            ),
            "metadata": metadata,
        }
        if run_checkpoint is not None:
            create_kwargs["checkpoint"] = run_checkpoint
        remote = await services.gateway.create(
            thread.id,
            settings.langgraph_assistant_id,
            strategy=body.strategy,
            **create_kwargs,
        )
    except InvalidBranch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="Agent Server rejected the run",
        ) from exc
    return run_view(remote, thread_id=thread.id)


@router.get("/runs", response_model=list[RunView])
async def list_runs(
    thread_id: str,
    user: UserDep,
    services: ServicesDep,
) -> list[RunView]:
    thread = await services.require_thread(thread_id, user)
    await services.project_for_thread(thread, user)
    try:
        return await services.gateway.runs(thread.id)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Agent Server could not list runs",
        ) from exc


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    thread_id: str,
    run_id: str,
    user: UserDep,
    services: ServicesDep,
) -> RunView:
    thread = await services.require_thread(thread_id, user)
    await services.project_for_thread(thread, user)
    try:
        return await services.gateway.run(thread.id, run_id)
    except Exception as exc:
        status = remote_status(exc)
        raise HTTPException(
            status_code=status,
            detail="run not found" if status == 404 else "Agent Server run is unavailable",
        ) from exc


@router.post("/runs/{run_id}/resume", response_model=RunView, status_code=202)
async def resume_run(
    thread_id: str,
    run_id: str,
    body: ResumeRun,
    user: UserDep,
    services: ServicesDep,
) -> RunView:
    thread = await services.require_thread(thread_id, user)
    project = await services.project_for_thread(thread, user)
    try:
        previous = await services.gateway.run(thread.id, run_id)
        state = await services.gateway.state(thread.id)
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="interrupted run is unavailable",
        ) from exc
    if previous.status != "interrupted" or not state_interrupts(state):
        raise HTTPException(status_code=409, detail="run is not waiting for input")

    settings = services.settings
    metadata = {
        "app_id": settings.app_id,
        "project_id": project.id,
        "owner_id": user.id,
        "thread_id": thread.id,
        "turn_id": previous.turn_id,
        "parent_run_id": previous.id,
        "app_version": settings.app_version,
        "environment": settings.app_environment,
    }
    try:
        remote = await services.gateway.create(
            thread.id,
            settings.langgraph_assistant_id,
            strategy="enqueue",
            command={"resume": body.value},
            context=services.run_context(
                user=user,
                project=project,
                thread=thread,
                turn_id=previous.turn_id,
            ),
            metadata=metadata,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="Agent Server rejected the resume",
        ) from exc
    return run_view(remote, thread_id=thread.id)


@router.post("/runs/{run_id}/cancel", status_code=204)
async def cancel_run(
    thread_id: str,
    run_id: str,
    user: UserDep,
    services: ServicesDep,
) -> Response:
    thread = await services.require_thread(thread_id, user)
    project = await services.project_for_thread(thread, user)
    try:
        run = await services.gateway.run(thread.id, run_id)
        await services.gateway.cancel(thread.id, run_id)
        await cancel_child_tasks(
            services.gateway,
            project_id=project.id,
            owner_id=user.id,
            parent_thread_id=thread.id,
            parent_turn_id=run.turn_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=remote_status(exc),
            detail="Agent Server could not cancel the run",
        ) from exc
    return Response(status_code=204)


@router.get("/snapshot", response_model=ThreadSnapshot)
async def get_snapshot(
    thread_id: str,
    user: UserDep,
    services: ServicesDep,
    checkpoint_id: str | None = None,
) -> ThreadSnapshot:
    thread = await services.require_thread(thread_id, user)
    project = await services.project_for_thread(thread, user)
    try:
        state, runs, history, thread_assets = await asyncio.gather(
            services.gateway.state(thread.id, checkpoint_id=checkpoint_id),
            services.gateway.runs(thread.id),
            services.gateway.history(thread.id),
            asyncio.to_thread(
                services.asset_store.list_assets,
                owner_id=user.id,
                project_id=project.id,
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="thread snapshot is unavailable",
        ) from exc
    selected_checkpoint_id = state_checkpoint_id(state)
    if selected_checkpoint_id and all(
        state_checkpoint_id(item) != selected_checkpoint_id for item in history
    ):
        history = [state, *history]
    branch = branch_state(history, state)
    selected_run_ids = branch_run_ids(history, branch.current_checkpoint_id)
    if selected_run_ids:
        runs = [
            run
            for run in runs
            if run.id in selected_run_ids or run.status in {"pending", "running"}
        ]
    internal_messages = state_messages(state)
    messages = normalize_messages(state)
    messages = [
        {
            **message,
            "content": editable_content(message.get("content")),
        }
        if message.get("role") == "user"
        else message
        for message in messages
    ]
    return ThreadSnapshot(
        thread=thread,
        runs=runs,
        messages=messages,
        activities=snapshot_activities(
            thread_id=thread.id,
            messages=internal_messages,
            runs=runs,
        ),
        todos=state_todos(state),
        interrupts=state_interrupts(state),
        widgets=state_widgets(internal_messages),
        usage=summarize_usage(
            internal_messages,
            input_cost_per_million=services.settings.openai_input_cost_per_million,
            output_cost_per_million=services.settings.openai_output_cost_per_million,
        ),
        assets=thread_assets,
        branch=branch,
    )


@router.get("/runs/{run_id}/stream")
async def stream_run(
    thread_id: str,
    run_id: str,
    request: Request,
    user: UserDep,
    services: ServicesDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    thread = await services.require_thread(thread_id, user)
    await services.project_for_thread(thread, user)
    try:
        await services.gateway.client.runs.get(thread.id, run_id)
    except Exception as exc:
        status = remote_status(exc)
        raise HTTPException(
            status_code=status,
            detail="run not found" if status == 404 else "Agent Server stream is unavailable",
        ) from exc

    async def generate() -> AsyncIterator[bytes]:
        terminal_cursor = bool(last_event_id and last_event_id.startswith(f"terminal:{run_id}:"))
        if not terminal_cursor:
            try:
                async for part in services.gateway.client.runs.join_stream(
                    thread.id,
                    run_id,
                    cancel_on_disconnect=False,
                    last_event_id=last_event_id,
                ):
                    if await request.is_disconnected():
                        return
                    event = normalize_stream_part(
                        part,
                        thread_id=thread.id,
                        graph_run_id=run_id,
                    )
                    try:
                        activity_events = project_activity_events(event)
                    except Exception:
                        logger.exception(
                            "Failed to project activity for run %s event %s",
                            run_id,
                            event.id,
                        )
                        activity_events = []
                    public_events = [
                        *project_public_events(event),
                        *activity_events,
                    ]
                    if not public_events:
                        public_events = [
                            AgentEvent(
                                id=f"cursor:{event.id}",
                                thread_id=event.thread_id,
                                run_id=event.run_id,
                                type="stream.cursor",
                                payload={},
                                created_at=event.created_at,
                            )
                        ]
                    for index, public_event in enumerate(public_events):
                        # The first public frame advances the upstream resumable cursor.
                        cursor_event = (
                            public_event.model_copy(update={"id": event.id})
                            if index == 0
                            else public_event
                        )
                        yield event_frame(cursor_event, include_cursor=index == 0)
            except Exception:
                logger.exception(
                    "Agent Server stream failed for thread %s run %s",
                    thread.id,
                    run_id,
                )
                return
        try:
            current = await services.gateway.run(thread.id, run_id)
        except Exception:
            return
        terminal = AgentEvent(
            id=f"terminal:{run_id}:{current.status}",
            thread_id=thread.id,
            run_id=run_id,
            type=f"run.{current.status}",
            payload=current.model_dump(mode="json"),
        )
        yield event_frame(terminal)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
