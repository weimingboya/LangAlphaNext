from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph_sdk import get_client

from langalpha.backends.daytona import get_daytona_backend_for_workspace
from langalpha.config import Settings, get_settings
from langalpha.domain.models import (
    Artifact,
    DomainEvent,
    Guidance,
    GuidanceCreate,
    GuidanceReturn,
    ProductRun,
    ProductThread,
    ResumeRun,
    RunCreate,
    RuntimeBinding,
    ThreadCreate,
)
from langalpha.server.outbox import RedisOutboxPublisher
from langalpha.server.repository import ActiveRunConflict, Repository
from langalpha.server.stream_bridge import RunStreamBridge


def _safe_filename(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip(".-")
    if not name:
        raise HTTPException(status_code=422, detail="invalid filename")
    return name[:160]


def _event_frame(event: DomainEvent) -> bytes:
    data = event.model_dump(mode="json")
    return (
        f"id: {event.sequence}\n"
        f"event: {event.type}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    ).encode()


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    repository = Repository(app_settings.langalpha_database_path)
    bridge = RunStreamBridge(
        repository,
        app_settings.langgraph_server_url,
        max_run_seconds=app_settings.max_run_seconds,
        max_reconnect_attempts=app_settings.stream_reconnect_attempts,
        reconnect_max_delay=app_settings.stream_reconnect_max_delay_seconds,
        input_cost_per_million=app_settings.openai_input_cost_per_million,
        output_cost_per_million=app_settings.openai_output_cost_per_million,
        cost_warning_usd=app_settings.cost_warning_usd,
    )
    outbox_publisher = (
        RedisOutboxPublisher(
            repository,
            app_settings.redis_url.get_secret_value(),
            channel_prefix=app_settings.redis_event_channel_prefix,
            poll_interval=app_settings.outbox_poll_interval_seconds,
        )
        if app_settings.redis_url is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await repository.initialize()
        if outbox_publisher is not None:
            outbox_publisher.start()
        await bridge.recover()
        try:
            yield
        finally:
            await bridge.close()
            if outbox_publisher is not None:
                await outbox_publisher.close()

    app = FastAPI(title="LangAlpha Local API", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.repository = repository
    app.state.bridge = bridge
    app.state.outbox_publisher = outbox_publisher
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/threads", response_model=ProductThread, status_code=201)
    async def create_thread(body: ThreadCreate) -> ProductThread:
        client = get_client(url=app_settings.langgraph_server_url)
        product_id = str(uuid4())
        graph_thread = await client.threads.create(
            metadata={
                "product_thread_id": product_id,
                "project_id": app_settings.langalpha_project_id,
                "owner_id": app_settings.langalpha_owner_id,
            }
        )
        graph_thread_id = str(graph_thread["thread_id"])
        return await repository.create_thread(
            graph_thread_id=graph_thread_id,
            workspace_id=product_id,
            title=body.title,
            thread_id=product_id,
            project_id=app_settings.langalpha_project_id,
            owner_id=app_settings.langalpha_owner_id,
            assistant_id=app_settings.langgraph_assistant_id,
        )

    @app.get("/api/threads", response_model=list[ProductThread])
    async def list_threads() -> list[ProductThread]:
        return await repository.list_threads()

    @app.get("/api/threads/{thread_id}", response_model=ProductThread)
    async def get_thread(thread_id: str) -> ProductThread:
        thread = await repository.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        return thread

    @app.get("/api/threads/{thread_id}/binding", response_model=RuntimeBinding)
    async def get_binding(thread_id: str) -> RuntimeBinding:
        binding = await repository.get_binding(thread_id)
        if binding is None:
            raise HTTPException(status_code=404, detail="runtime binding not found")
        return binding

    def run_context(
        *,
        thread: ProductThread,
        binding: RuntimeBinding,
        turn_id: str,
        product_run_id: str,
    ) -> dict[str, str | None]:
        return {
            "project_id": binding.project_id,
            "owner_id": binding.owner_id,
            "workspace_id": binding.workspace_id,
            "product_thread_id": thread.id,
            "turn_id": turn_id,
            "product_run_id": product_run_id,
            "capability_profile": binding.profile,
            "expected_sandbox_id": binding.sandbox_id,
        }

    def verify_internal(authorization: str | None) -> None:
        configured = app_settings.langalpha_internal_token
        if configured is None:
            return
        expected = f"Bearer {configured.get_secret_value()}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid internal token")

    @app.post("/api/threads/{thread_id}/runs", response_model=ProductRun, status_code=202)
    async def create_run(thread_id: str, body: RunCreate) -> ProductRun:
        thread = await repository.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        binding = await repository.get_binding(thread_id)
        if binding is None:
            raise HTTPException(status_code=409, detail="runtime binding not found")

        for open_run in await repository.list_open_runs(thread.id):
            if open_run.graph_run_id is not None and open_run.status in {
                "pending",
                "running",
            }:
                try:
                    open_run = await bridge.remote_status(thread, open_run)
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="cannot verify the active Agent Server run",
                    ) from exc
            if open_run.status in {"pending", "running", "interrupted"}:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "thread already has an active run; resume an interrupt "
                        "instead of starting a new turn"
                    ),
                )

        product_run_id = str(uuid4())
        turn_id = str(uuid4())
        await repository.append_event(
            thread_id=thread.id,
            run_id=None,
            event_type="user.message",
            payload={"content": body.message, "turn_id": turn_id},
            source_event_key=f"turn:{turn_id}:user-message",
        )
        try:
            run = await repository.create_run(
                thread_id=thread.id,
                graph_run_id=None,
                run_id=product_run_id,
                turn_id=turn_id,
            )
        except ActiveRunConflict as exc:
            raise HTTPException(
                status_code=409,
                detail="thread already has an active run",
            ) from exc
        client = get_client(url=app_settings.langgraph_server_url)
        try:
            remote = await client.runs.create(
                thread.graph_thread_id,
                binding.assistant_id,
                input={"messages": [{"role": "user", "content": body.message}]},
                context=run_context(
                    thread=thread,
                    binding=binding,
                    turn_id=turn_id,
                    product_run_id=product_run_id,
                ),
                metadata={
                    "product_thread_id": thread.id,
                    "product_run_id": product_run_id,
                    "turn_id": turn_id,
                },
                stream_mode=["messages", "updates", "custom"],
                stream_subgraphs=True,
                stream_resumable=True,
            )
            run = await repository.attach_runtime_run(run.id, str(remote["run_id"]))
        except Exception as exc:
            error = type(exc).__name__
            await repository.update_run(run.id, "error", error=error)
            await repository.append_event(
                thread_id=thread.id,
                run_id=run.id,
                event_type="run.error",
                payload={"run_id": run.id, "error": error},
                source_event_key=f"product:{run.id}:create-error",
            )
            raise HTTPException(
                status_code=502,
                detail="Agent Server run creation failed",
            ) from exc
        bridge.watch(thread, run)
        return run

    @app.get("/api/runs/{run_id}", response_model=ProductRun)
    async def get_run(run_id: str) -> ProductRun:
        run = await repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        thread = await repository.get_thread(run.thread_id)
        assert thread is not None
        try:
            return await bridge.remote_status(thread, run)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="Agent Server status is unavailable"
            ) from exc

    @app.post("/api/runs/{run_id}/resume", response_model=ProductRun, status_code=202)
    async def resume_run(run_id: str, body: ResumeRun) -> ProductRun:
        previous = await repository.get_run(run_id)
        if previous is None:
            raise HTTPException(status_code=404, detail="run not found")
        thread = await repository.get_thread(previous.thread_id)
        assert thread is not None
        binding = await repository.get_binding(thread.id)
        assert binding is not None
        previous = await bridge.remote_status(thread, previous)
        if previous.status != "interrupted":
            raise HTTPException(status_code=409, detail="run is not interrupted")

        product_run_id = str(uuid4())
        try:
            run = await repository.create_run(
                thread_id=thread.id,
                graph_run_id=None,
                run_id=product_run_id,
                turn_id=previous.turn_id,
                parent_run_id=previous.id,
            )
        except ActiveRunConflict as exc:
            raise HTTPException(
                status_code=409,
                detail="thread already has an active run",
            ) from exc
        client = get_client(url=app_settings.langgraph_server_url)
        try:
            remote = await client.runs.create(
                thread.graph_thread_id,
                binding.assistant_id,
                command={"resume": body.value},
                context=run_context(
                    thread=thread,
                    binding=binding,
                    turn_id=previous.turn_id,
                    product_run_id=product_run_id,
                ),
                metadata={
                    "product_thread_id": thread.id,
                    "product_run_id": product_run_id,
                    "turn_id": previous.turn_id,
                    "parent_product_run_id": previous.id,
                },
                stream_mode=["messages", "updates", "custom"],
                stream_subgraphs=True,
                stream_resumable=True,
            )
            run = await repository.attach_runtime_run(run.id, str(remote["run_id"]))
        except Exception as exc:
            error = type(exc).__name__
            await repository.update_run(run.id, "error", error=error)
            await repository.append_event(
                thread_id=thread.id,
                run_id=run.id,
                event_type="run.error",
                payload={"run_id": run.id, "error": error},
                source_event_key=f"product:{run.id}:resume-error",
            )
            raise HTTPException(
                status_code=502,
                detail="Agent Server resume failed",
            ) from exc
        await repository.append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="interrupt.resumed",
            payload={"parent_run_id": previous.id, "run_id": run.id},
            source_event_key=f"runtime:{run.graph_run_id}:resume",
        )
        bridge.watch(thread, run)
        return run

    @app.post("/api/runs/{run_id}/cancel", status_code=204)
    async def cancel_run(run_id: str) -> Response:
        run = await repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run.graph_run_id is None:
            raise HTTPException(status_code=409, detail="runtime run is not attached")
        thread = await repository.get_thread(run.thread_id)
        assert thread is not None
        client = get_client(url=app_settings.langgraph_server_url)
        await repository.set_cancel_requested(run.id, True)
        try:
            await client.runs.cancel(
                thread.graph_thread_id,
                run.graph_run_id,
                wait=True,
            )
            run = await bridge.remote_status(thread, run)
        except Exception as exc:
            await repository.set_cancel_requested(run.id, False)
            raise HTTPException(
                status_code=502,
                detail="Agent Server cancellation failed",
            ) from exc
        if run.status != "cancelled":
            await repository.set_cancel_requested(run.id, False)
            raise HTTPException(
                status_code=409,
                detail=f"run reached terminal status {run.status} before cancellation",
            )
        await repository.append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="run.cancelled",
            payload={"run_id": run.id},
            source_event_key=f"runtime:{run.graph_run_id}:terminal:cancelled",
        )
        return Response(status_code=204)

    @app.post(
        "/api/runs/{run_id}/guidance",
        response_model=Guidance,
        status_code=202,
    )
    async def add_guidance(run_id: str, body: GuidanceCreate) -> Guidance:
        run = await repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        thread = await repository.get_thread(run.thread_id)
        assert thread is not None
        run = await bridge.remote_status(thread, run)
        if run.status not in {"pending", "running"}:
            raise HTTPException(status_code=409, detail="run no longer accepts guidance")
        guidance = await repository.create_guidance(
            thread_id=thread.id,
            run_id=run.id,
            message=body.message,
        )
        await repository.append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="steering.accepted",
            payload=guidance.model_dump(mode="json"),
            source_event_key=f"guidance:{guidance.id}:accepted",
        )
        return guidance

    @app.post(
        "/internal/runs/{run_id}/guidance/claim",
        response_model=list[Guidance],
        include_in_schema=False,
    )
    async def claim_guidance(
        run_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[Guidance]:
        verify_internal(authorization)
        return await repository.claim_guidance(run_id)

    @app.post(
        "/internal/runs/{run_id}/guidance/return",
        status_code=204,
        include_in_schema=False,
    )
    async def return_guidance(
        run_id: str,
        body: GuidanceReturn,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        verify_internal(authorization)
        await repository.return_guidance(run_id, body.ids)
        return Response(status_code=204)

    @app.get(
        "/internal/threads/{thread_id}/state",
        include_in_schema=False,
    )
    async def get_runtime_state(
        thread_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict:
        verify_internal(authorization)
        thread = await repository.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        client = get_client(url=app_settings.langgraph_server_url)
        return await client.threads.get_state(thread.graph_thread_id)

    @app.get(
        "/internal/threads/{thread_id}/history",
        include_in_schema=False,
    )
    async def get_runtime_history(
        thread_id: str,
        limit: int = 20,
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict]:
        verify_internal(authorization)
        thread = await repository.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        client = get_client(url=app_settings.langgraph_server_url)
        return await client.threads.get_history(
            thread.graph_thread_id,
            limit=max(1, min(limit, 100)),
        )

    @app.get("/api/threads/{thread_id}/events")
    async def stream_events(
        thread_id: str,
        request: Request,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        if await repository.get_thread(thread_id) is None:
            raise HTTPException(status_code=404, detail="thread not found")
        try:
            cursor = int(last_event_id or request.query_params.get("after", "0"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid event cursor") from exc

        async def generate() -> AsyncIterator[bytes]:
            nonlocal cursor
            heartbeat_at = asyncio.get_running_loop().time()
            while not await request.is_disconnected():
                events = await repository.list_events(thread_id, after_sequence=cursor)
                for event in events:
                    cursor = event.sequence
                    yield _event_frame(event)
                now = asyncio.get_running_loop().time()
                if now - heartbeat_at >= 15:
                    yield b": heartbeat\n\n"
                    heartbeat_at = now
                await asyncio.sleep(0.35)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(
        "/api/threads/{thread_id}/files",
        response_model=Artifact,
        status_code=201,
    )
    async def upload_file(
        thread_id: str,
        file: Annotated[UploadFile, File()],
    ) -> Artifact:
        thread = await repository.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        content = await file.read()
        if len(content) > app_settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="file exceeds upload limit")
        name = _safe_filename(file.filename or "upload.bin")
        path = f"/workspace/uploads/{uuid4()}-{name}"
        backend = await asyncio.to_thread(
            get_daytona_backend_for_workspace,
            workspace_id=thread.workspace_id,
            project_id=app_settings.langalpha_project_id,
            expected_sandbox_id=(
                (await repository.get_binding(thread.id)).sandbox_id  # type: ignore[union-attr]
            ),
        )
        await asyncio.to_thread(backend.execute, "mkdir -p /workspace/uploads")
        responses = await asyncio.to_thread(backend.upload_files, [(path, content)])
        if not responses or responses[0].error:
            detail = responses[0].error if responses else "empty upload response"
            raise HTTPException(status_code=502, detail=f"sandbox upload failed: {detail}")
        await repository.bind_sandbox(thread.id, backend.id)
        artifact = await repository.upsert_artifact(
            thread_id=thread.id,
            run_id=None,
            name=name,
            sandbox_path=path,
            media_type=file.content_type or "application/octet-stream",
            size_bytes=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
        )
        await repository.append_event(
            thread_id=thread.id,
            run_id=None,
            event_type="artifact.created",
            payload=artifact.model_dump(mode="json"),
            source_event_key=f"artifact:{artifact.id}:{artifact.checksum}",
        )
        return artifact

    @app.get("/api/threads/{thread_id}/artifacts", response_model=list[Artifact])
    async def list_artifacts(thread_id: str) -> list[Artifact]:
        if await repository.get_thread(thread_id) is None:
            raise HTTPException(status_code=404, detail="thread not found")
        return await repository.list_artifacts(thread_id)

    @app.get("/api/artifacts/{artifact_id}")
    async def download_artifact(artifact_id: str) -> Response:
        artifact = await repository.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        thread = await repository.get_thread(artifact.thread_id)
        assert thread is not None
        backend = await asyncio.to_thread(
            get_daytona_backend_for_workspace,
            workspace_id=thread.workspace_id,
            project_id=app_settings.langalpha_project_id,
            expected_sandbox_id=(
                (await repository.get_binding(thread.id)).sandbox_id  # type: ignore[union-attr]
            ),
        )
        responses = await asyncio.to_thread(backend.download_files, [artifact.sandbox_path])
        result = responses[0]
        if result.error or result.content is None:
            raise HTTPException(status_code=404, detail="sandbox file not found")
        return Response(
            content=result.content,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": (f'attachment; filename="{_safe_filename(artifact.name)}"')
            },
        )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("langalpha.server.main:app", host="127.0.0.1", port=8000, reload=True)
