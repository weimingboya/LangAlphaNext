from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from langalpha.backends.daytona import (
    get_daytona_backend_for_workspace,
    list_artifact_manifest,
)
from langalpha.config import Settings, get_settings
from langalpha.domain.models import (
    AgentEvent,
    Artifact,
    Guidance,
    GuidanceCreate,
    GuidanceReturn,
    ProductThread,
    ResumeRun,
    RunCreate,
    RuntimeBinding,
    RunView,
    ThreadCreate,
    ThreadSnapshot,
    utc_now,
)
from langalpha.server.agent_gateway import (
    AgentGateway,
    normalize_messages,
    normalize_stream_part,
    run_view,
    state_interrupts,
    state_todos,
    state_widgets,
    summarize_usage,
)
from langalpha.server.repository import Repository


def _safe_filename(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip(".-")
    if not name:
        raise HTTPException(status_code=422, detail="invalid filename")
    return name[:160]


def _event_frame(event: AgentEvent) -> bytes:
    return (f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n").encode()


def _agent_error_status(exc: Exception) -> int:
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    return 409 if status == 409 else 502


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    repository = Repository(app_settings.langalpha_database_path)
    gateway = AgentGateway(app_settings.langgraph_server_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await repository.initialize()
        yield

    app = FastAPI(title="LangAlpha BFF", version="0.2.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.repository = repository
    app.state.agent_gateway = gateway
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def require_thread(thread_id: str) -> ProductThread:
        thread = await repository.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        return thread

    async def require_binding(thread_id: str) -> RuntimeBinding:
        binding = await repository.get_binding(thread_id)
        if binding is None:
            raise HTTPException(status_code=404, detail="runtime binding not found")
        return binding

    def run_context(
        *,
        thread: ProductThread,
        binding: RuntimeBinding,
        turn_id: str,
        control_id: str,
    ) -> dict[str, str | None]:
        return {
            "project_id": binding.project_id,
            "owner_id": binding.owner_id,
            "workspace_id": binding.workspace_id,
            "product_thread_id": thread.id,
            "turn_id": turn_id,
            "product_run_id": control_id,
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

    async def backend_for(thread: ProductThread) -> Any:
        binding = await require_binding(thread.id)
        backend = await asyncio.to_thread(
            get_daytona_backend_for_workspace,
            workspace_id=thread.workspace_id,
            project_id=app_settings.langalpha_project_id,
            expected_sandbox_id=binding.sandbox_id,
        )
        await repository.bind_sandbox(thread.id, backend.id)
        return backend

    async def reconcile_artifacts(thread: ProductThread) -> list[Artifact]:
        binding = await require_binding(thread.id)
        if binding.sandbox_id is None:
            return await repository.list_artifacts(thread.id)
        backend = await backend_for(thread)
        manifest = await asyncio.to_thread(list_artifact_manifest, backend)
        for path, item in manifest.items():
            await repository.upsert_artifact(
                thread_id=thread.id,
                run_id=None,
                name=Path(path).name,
                sandbox_path=path,
                media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                size_bytes=int(item.get("size_bytes", 0)),
                checksum=(str(item["checksum"]) if item.get("checksum") else None),
            )
        return await repository.list_artifacts(thread.id)

    @app.post("/api/threads", response_model=ProductThread, status_code=201)
    async def create_thread(body: ThreadCreate) -> ProductThread:
        product_id = str(uuid4())
        graph_thread = await gateway.client.threads.create(
            metadata={
                "product_thread_id": product_id,
                "project_id": app_settings.langalpha_project_id,
                "owner_id": app_settings.langalpha_owner_id,
            }
        )
        return await repository.create_thread(
            graph_thread_id=str(graph_thread["thread_id"]),
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
        return await require_thread(thread_id)

    @app.get("/api/threads/{thread_id}/binding", response_model=RuntimeBinding)
    async def get_binding(thread_id: str) -> RuntimeBinding:
        await require_thread(thread_id)
        return await require_binding(thread_id)

    @app.post(
        "/api/threads/{thread_id}/runs",
        response_model=RunView,
        status_code=202,
    )
    async def create_run(thread_id: str, body: RunCreate) -> RunView:
        thread = await require_thread(thread_id)
        binding = await require_binding(thread_id)
        try:
            state = await gateway.state(thread.graph_thread_id)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="Agent Server state is unavailable"
            ) from exc
        if state_interrupts(state):
            raise HTTPException(
                status_code=409,
                detail="thread is waiting for input; resume its interrupted run",
            )

        control_id = str(uuid4())
        turn_id = str(uuid4())
        metadata = {
            "product_thread_id": thread.id,
            "product_run_id": control_id,
            "turn_id": turn_id,
        }
        try:
            remote = await gateway.create(
                thread.graph_thread_id,
                binding.assistant_id,
                input={"messages": [{"role": "user", "content": body.message}]},
                context=run_context(
                    thread=thread,
                    binding=binding,
                    turn_id=turn_id,
                    control_id=control_id,
                ),
                metadata=metadata,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=_agent_error_status(exc),
                detail=(
                    "thread already has an active Agent Server run"
                    if _agent_error_status(exc) == 409
                    else "Agent Server run creation failed"
                ),
            ) from exc
        remote.setdefault("metadata", metadata)
        remote.setdefault("status", "pending")
        remote.setdefault("created_at", utc_now())
        await repository.touch_thread(thread.id)
        return run_view(remote, product_thread_id=thread.id)

    @app.get("/api/threads/{thread_id}/runs", response_model=list[RunView])
    async def list_runs(thread_id: str, limit: int = 50) -> list[RunView]:
        thread = await require_thread(thread_id)
        try:
            return await gateway.runs(
                thread.graph_thread_id,
                product_thread_id=thread.id,
                limit=max(1, min(limit, 100)),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="Agent Server runs are unavailable"
            ) from exc

    @app.get(
        "/api/threads/{thread_id}/runs/{run_id}",
        response_model=RunView,
    )
    async def get_run(thread_id: str, run_id: str) -> RunView:
        thread = await require_thread(thread_id)
        try:
            return await gateway.run(
                thread.graph_thread_id,
                run_id,
                product_thread_id=thread.id,
            )
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise HTTPException(
                status_code=404 if status == 404 else 502,
                detail="run not found" if status == 404 else "Agent Server status is unavailable",
            ) from exc

    @app.post(
        "/api/threads/{thread_id}/runs/{run_id}/resume",
        response_model=RunView,
        status_code=202,
    )
    async def resume_run(thread_id: str, run_id: str, body: ResumeRun) -> RunView:
        thread = await require_thread(thread_id)
        binding = await require_binding(thread_id)
        try:
            previous = await gateway.run(
                thread.graph_thread_id,
                run_id,
                product_thread_id=thread.id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="Agent Server status is unavailable"
            ) from exc
        if previous.status != "interrupted":
            raise HTTPException(status_code=409, detail="run is not interrupted")

        control_id = str(uuid4())
        metadata = {
            "product_thread_id": thread.id,
            "product_run_id": control_id,
            "turn_id": previous.turn_id,
            "parent_run_id": previous.id,
        }
        try:
            remote = await gateway.create(
                thread.graph_thread_id,
                binding.assistant_id,
                command={"resume": body.value},
                context=run_context(
                    thread=thread,
                    binding=binding,
                    turn_id=previous.turn_id,
                    control_id=control_id,
                ),
                metadata=metadata,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=_agent_error_status(exc),
                detail="Agent Server resume failed",
            ) from exc
        remote.setdefault("metadata", metadata)
        remote.setdefault("status", "pending")
        remote.setdefault("created_at", utc_now())
        await repository.transfer_open_guidance(previous.control_id, control_id)
        await repository.touch_thread(thread.id)
        return run_view(remote, product_thread_id=thread.id)

    @app.post(
        "/api/threads/{thread_id}/runs/{run_id}/cancel",
        status_code=204,
    )
    async def cancel_run(thread_id: str, run_id: str) -> Response:
        thread = await require_thread(thread_id)
        try:
            current = await gateway.run(
                thread.graph_thread_id,
                run_id,
                product_thread_id=thread.id,
            )
            if current.status not in {"pending", "running"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"run no longer accepts cancellation ({current.status})",
                )
            await gateway.cancel(thread.graph_thread_id, run_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Agent Server cancellation failed") from exc
        return Response(status_code=204)

    @app.post(
        "/api/threads/{thread_id}/runs/{run_id}/guidance",
        response_model=Guidance,
        status_code=202,
    )
    async def add_guidance(
        thread_id: str,
        run_id: str,
        body: GuidanceCreate,
    ) -> Guidance:
        thread = await require_thread(thread_id)
        try:
            current = await gateway.run(
                thread.graph_thread_id,
                run_id,
                product_thread_id=thread.id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="Agent Server status is unavailable"
            ) from exc
        if current.status not in {"pending", "running"}:
            raise HTTPException(status_code=409, detail="run no longer accepts guidance")
        return await repository.create_guidance(
            thread_id=thread.id,
            run_id=current.control_id,
            message=body.message,
        )

    @app.get("/api/threads/{thread_id}/snapshot", response_model=ThreadSnapshot)
    async def get_snapshot(thread_id: str) -> ThreadSnapshot:
        thread = await require_thread(thread_id)
        try:
            state, runs = await asyncio.gather(
                gateway.state(thread.graph_thread_id),
                gateway.runs(
                    thread.graph_thread_id,
                    product_thread_id=thread.id,
                    limit=100,
                ),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="Agent Server snapshot is unavailable"
            ) from exc
        messages = normalize_messages(state)
        artifacts = await reconcile_artifacts(thread)
        return ThreadSnapshot(
            thread=thread,
            runs=runs,
            messages=messages,
            todos=state_todos(state),
            interrupts=state_interrupts(state),
            widgets=state_widgets(messages),
            usage=summarize_usage(
                messages,
                input_cost_per_million=app_settings.openai_input_cost_per_million,
                output_cost_per_million=app_settings.openai_output_cost_per_million,
            ),
            artifacts=artifacts,
        )

    @app.get("/api/threads/{thread_id}/runs/{run_id}/stream")
    async def stream_run(
        thread_id: str,
        run_id: str,
        request: Request,
        last_event_id: Annotated[
            str | None,
            Header(alias="Last-Event-ID"),
        ] = None,
    ) -> StreamingResponse:
        thread = await require_thread(thread_id)
        try:
            await gateway.client.runs.get(thread.graph_thread_id, run_id)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise HTTPException(
                status_code=404 if status == 404 else 502,
                detail="run not found" if status == 404 else "Agent Server stream is unavailable",
            ) from exc

        async def generate() -> AsyncIterator[bytes]:
            terminal_cursor = bool(
                last_event_id and last_event_id.startswith(f"terminal:{run_id}:")
            )
            if not terminal_cursor:
                try:
                    async for part in gateway.client.runs.join_stream(
                        thread.graph_thread_id,
                        run_id,
                        cancel_on_disconnect=False,
                        last_event_id=last_event_id,
                    ):
                        if await request.is_disconnected():
                            return
                        event = normalize_stream_part(
                            part,
                            product_thread_id=thread.id,
                            graph_run_id=run_id,
                        )
                        if event.type == "sandbox.bound":
                            sandbox_id = event.payload.get("sandbox_id")
                            if isinstance(sandbox_id, str):
                                await repository.bind_sandbox(thread.id, sandbox_id)
                        elif event.type == "artifact.updated":
                            path = str(event.payload.get("path") or "")
                            if path.startswith("/workspace/artifacts/"):
                                artifact = await repository.upsert_artifact(
                                    thread_id=thread.id,
                                    run_id=run_id,
                                    name=str(event.payload.get("name") or Path(path).name),
                                    sandbox_path=path,
                                    media_type=str(
                                        event.payload.get("media_type")
                                        or "application/octet-stream"
                                    ),
                                    size_bytes=int(event.payload.get("size_bytes") or 0),
                                    checksum=(
                                        str(event.payload["checksum"])
                                        if event.payload.get("checksum")
                                        else None
                                    ),
                                )
                                event.payload = artifact.model_dump(mode="json")
                        yield _event_frame(event)
                except Exception:
                    # EventSource reconnects with Last-Event-ID. Agent Server is
                    # the cursor authority, so this BFF intentionally stores no
                    # reconnect state and emits no false terminal event.
                    return
            try:
                current = await gateway.run(
                    thread.graph_thread_id,
                    run_id,
                    product_thread_id=thread.id,
                )
            except Exception:
                return
            terminal = AgentEvent(
                id=f"terminal:{run_id}:{current.status}",
                thread_id=thread.id,
                run_id=run_id,
                type=f"run.{current.status}",
                payload=current.model_dump(mode="json"),
            )
            yield _event_frame(terminal)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

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

    @app.get("/internal/threads/{thread_id}/state", include_in_schema=False)
    async def get_runtime_state(
        thread_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        verify_internal(authorization)
        thread = await require_thread(thread_id)
        return await gateway.state(thread.graph_thread_id)

    @app.get("/internal/threads/{thread_id}/history", include_in_schema=False)
    async def get_runtime_history(
        thread_id: str,
        limit: int = 20,
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, Any]]:
        verify_internal(authorization)
        thread = await require_thread(thread_id)
        history = await gateway.client.threads.get_history(
            thread.graph_thread_id,
            limit=max(1, min(limit, 100)),
        )
        return [
            item if isinstance(item, dict) else item.model_dump(mode="json") for item in history
        ]

    @app.post(
        "/api/threads/{thread_id}/files",
        response_model=Artifact,
        status_code=201,
    )
    async def upload_file(
        thread_id: str,
        file: Annotated[UploadFile, File()],
    ) -> Artifact:
        thread = await require_thread(thread_id)
        content = await file.read()
        if len(content) > app_settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="file exceeds upload limit")
        name = _safe_filename(file.filename or "upload.bin")
        path = f"/workspace/uploads/{uuid4()}-{name}"
        backend = await backend_for(thread)
        await asyncio.to_thread(backend.execute, "mkdir -p /workspace/uploads")
        responses = await asyncio.to_thread(backend.upload_files, [(path, content)])
        if not responses or responses[0].error:
            detail = responses[0].error if responses else "empty upload response"
            raise HTTPException(status_code=502, detail=f"sandbox upload failed: {detail}")
        return await repository.upsert_artifact(
            thread_id=thread.id,
            run_id=None,
            name=name,
            sandbox_path=path,
            media_type=file.content_type or "application/octet-stream",
            size_bytes=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
        )

    @app.get("/api/threads/{thread_id}/artifacts", response_model=list[Artifact])
    async def list_artifacts(thread_id: str) -> list[Artifact]:
        thread = await require_thread(thread_id)
        return await reconcile_artifacts(thread)

    @app.get("/api/artifacts/{artifact_id}")
    async def download_artifact(artifact_id: str) -> Response:
        artifact = await repository.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        thread = await require_thread(artifact.thread_id)
        backend = await backend_for(thread)
        responses = await asyncio.to_thread(backend.download_files, [artifact.sandbox_path])
        result = responses[0]
        if result.error or result.content is None:
            raise HTTPException(status_code=404, detail="sandbox file not found")
        return Response(
            content=result.content,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{_safe_filename(artifact.name)}"'
            },
        )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("langalpha.server.main:app", host="127.0.0.1", port=8000, reload=True)
