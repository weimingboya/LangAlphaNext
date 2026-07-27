from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import threading
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from daytona import (
    CreateSandboxFromSnapshotParams,
    Daytona,
    DaytonaConfig,
    ListSandboxesQuery,
    SandboxState,
)
from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox
from langchain_daytona import DaytonaSandbox
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime
from langgraph_sdk import get_sync_client

from langalpha.agent.context import RunContext
from langalpha.assets.store import SupabaseAssetStore, safe_filename
from langalpha.config import get_settings

_LOCK = threading.RLock()
_CLIENT: Daytona | None = None
_RESOLVED_BACKENDS: dict[tuple[str, str], WorkspaceMappedDaytonaSandbox] = {}
_BOUND_EVENTS: set[tuple[str, str]] = set()
_PERSISTED_BINDINGS: set[tuple[str, str]] = set()
_HYDRATED_INPUTS: set[tuple[str, str, tuple[str, ...]]] = set()
_RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"
_ARTIFACTS_ROOT = "/workspace/artifacts"
_VIRTUAL_WORKSPACE = "/workspace"
_COMMAND_WORKSPACE_PATTERN = re.compile(r"(?<![A-Za-z0-9_.-])/workspace(?=$|[/\s'\";:),\]}])")
_MANIFEST_COMMAND = """python - <<'PY'
import json
from pathlib import Path

root = Path("/workspace/artifacts")
if root.exists():
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        stat = path.stat()
        print(json.dumps({
            "path": "/" + path.as_posix().lstrip("/"),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }, separators=(",", ":")))
PY"""


def _sandbox_name(thread_id: str) -> str:
    readable = re.sub(r"[^a-z0-9-]+", "-", thread_id.lower()).strip("-")[:24]
    digest = sha256(thread_id.encode()).hexdigest()[:10]
    return f"langalpha-{readable or 'thread'}-{digest}"[:63]


def _client() -> Daytona:
    global _CLIENT
    with _LOCK:
        if _CLIENT is None:
            settings = get_settings()
            _CLIENT = Daytona(
                DaytonaConfig(
                    api_key=settings.require_daytona_key(),
                    target=settings.daytona_target,
                )
            )
        return _CLIENT


def _ensure_started(sandbox: object) -> None:
    state = getattr(sandbox, "state", None)
    if state not in {SandboxState.STARTED, "started"}:
        sandbox.start(timeout=120)  # type: ignore[attr-defined]


def _emit_custom(payload: dict[str, object]) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer(payload)


def _persist_sandbox_binding(context: RunContext, sandbox_id: str) -> None:
    marker = (context.thread_id, sandbox_id)
    with _LOCK:
        if marker in _PERSISTED_BINDINGS:
            return
    client = get_sync_client(url=get_settings().langgraph_server_url)
    remote = client.threads.get(context.thread_id)
    metadata = remote.get("metadata") if isinstance(remote, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    if (
        metadata.get("owner_id") != context.owner_id
        or metadata.get("project_id") != context.project_id
    ):
        raise RuntimeError("LangGraph thread metadata does not match RunContext")
    current = metadata.get("sandbox_id")
    if current not in {None, sandbox_id}:
        raise RuntimeError("LangGraph thread is bound to another Daytona sandbox")
    if current is None:
        client.threads.update(
            context.thread_id,
            metadata={**metadata, "sandbox_id": sandbox_id},
        )
    with _LOCK:
        _PERSISTED_BINDINGS.add(marker)


def list_artifact_manifest(
    backend: WorkspaceMappedDaytonaSandbox,
) -> dict[str, dict[str, object]]:
    try:
        response = backend.execute(_MANIFEST_COMMAND, timeout=120)
    except TypeError:
        # Small test doubles and protocol-compatible adapters may omit the
        # optional timeout keyword.
        response = backend.execute(_MANIFEST_COMMAND)
    if response is None or getattr(response, "exit_code", 1) != 0:
        return {}
    manifest: dict[str, dict[str, object]] = {}
    for line in response.output.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = item.get("path")
        virtualize = getattr(backend, "_virtualize_text", None)
        if isinstance(path, str) and callable(virtualize):
            path = virtualize(path)
            item["path"] = path
        if isinstance(path, str) and path.startswith(f"{_ARTIFACTS_ROOT}/"):
            manifest[path] = item
    return manifest


@lru_cache(maxsize=1)
def _asset_store() -> SupabaseAssetStore:
    return SupabaseAssetStore(get_settings())


def _publish_artifact(
    *,
    context: RunContext,
    backend: WorkspaceMappedDaytonaSandbox,
    path: str,
    content: bytes | str | None = None,
) -> None:
    try:
        if content is None:
            result = backend.download_files([path])[0]
            if result.error or result.content is None:
                raise RuntimeError(result.error or "artifact download returned no content")
            content = result.content
        payload = content.encode() if isinstance(content, str) else content
        asset = _asset_store().publish_artifact(
            owner_id=context.owner_id,
            thread_id=context.thread_id,
            turn_id=context.turn_id,
            sandbox_path=path,
            content=payload,
            media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
        )
    except Exception as exc:
        _emit_custom(
            {
                "type": "asset.failed",
                "path": path,
                "error": type(exc).__name__,
            }
        )
        raise
    _emit_custom({"type": "asset.ready", **asset.model_dump(mode="json")})


def _run_context(runtime: object) -> RunContext:
    context = getattr(runtime, "context", None)
    if not isinstance(context, RunContext):
        raise RuntimeError("RunContext is required for persistent storage isolation")
    return context


def _user_memory_namespace(runtime: object) -> tuple[str, ...]:
    context = _run_context(runtime)
    return (context.project_id, context.owner_id, "memory")


def _thread_memory_namespace(runtime: object) -> tuple[str, ...]:
    context = _run_context(runtime)
    return (
        context.project_id,
        context.owner_id,
        "threads",
        context.thread_id,
        "memory",
    )


def _memo_namespace(runtime: object) -> tuple[str, ...]:
    context = _run_context(runtime)
    return (context.project_id, context.owner_id, "memos")


class WorkspaceMappedDaytonaSandbox(BaseSandbox):
    """Map the product's stable /workspace path onto Daytona's writable work dir.

    Daytona's current default image runs as an unprivileged user and does not
    allow creating a root-level /workspace directory. Core execution and file
    transfer remain owned by the official DaytonaSandbox integration; this
    adapter only translates the product path shape.
    """

    def __init__(self, delegate: DaytonaSandbox, physical_root: str) -> None:
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", physical_root):
            raise ValueError("Daytona work directory contains unsupported characters")
        self.delegate = delegate
        self.physical_root = physical_root.rstrip("/")

    @property
    def id(self) -> str:
        return self.delegate.id

    def _physical_path(self, path: str) -> str | None:
        if path == _VIRTUAL_WORKSPACE:
            return self.physical_root
        if path.startswith(f"{_VIRTUAL_WORKSPACE}/"):
            return f"{self.physical_root}{path[len(_VIRTUAL_WORKSPACE) :]}"
        return None

    def _command(self, command: str) -> str:
        return _COMMAND_WORKSPACE_PATTERN.sub(self.physical_root, command)

    def _virtualize_text(self, value: str | None) -> str | None:
        return value.replace(self.physical_root, _VIRTUAL_WORKSPACE) if value else value

    def _virtualize_file_info(
        self,
        item: dict[str, object],
    ) -> dict[str, object]:
        mapped = dict(item)
        path = mapped.get("path")
        if isinstance(path, str):
            mapped["path"] = self._virtualize_text(path)
        return mapped

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        return self.delegate.execute(self._command(command), timeout=timeout)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        valid: list[tuple[str, str, bytes]] = []
        responses: list[FileUploadResponse] = []
        for path, content in files:
            physical = self._physical_path(path)
            if physical is None:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            valid.append((path, physical, content))
            responses.append(FileUploadResponse(path=path, error=None))

        if valid:
            delegated = self.delegate.upload_files(
                [(physical, content) for _path, physical, content in valid]
            )
            delegated_iter = iter(delegated)
            for index, (path, _content) in enumerate(files):
                if self._physical_path(path) is None:
                    continue
                result = next(delegated_iter)
                responses[index] = FileUploadResponse(path=path, error=result.error)
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        valid: list[tuple[str, str]] = []
        responses: list[FileDownloadResponse] = []
        for path in paths:
            physical = self._physical_path(path)
            if physical is None:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue
            valid.append((path, physical))
            responses.append(FileDownloadResponse(path=path, content=None, error=None))

        if valid:
            delegated = self.delegate.download_files([physical for _path, physical in valid])
            delegated_iter = iter(delegated)
            for index, path in enumerate(paths):
                if self._physical_path(path) is None:
                    continue
                result = next(delegated_iter)
                responses[index] = FileDownloadResponse(
                    path=path,
                    content=result.content,
                    error=result.error,
                )
        return responses

    def ls(self, path: str) -> LsResult:
        physical = self._physical_path(path)
        if physical is None:
            return LsResult(error=f"Path '{path}': invalid_path")
        result = self.delegate.ls(physical)
        entries = (
            [self._virtualize_file_info(item) for item in result.entries]
            if result.entries is not None
            else None
        )
        return LsResult(error=self._virtualize_text(result.error), entries=entries)

    async def als(self, path: str) -> LsResult:
        return await asyncio.to_thread(self.ls, path)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        physical = self._physical_path(file_path)
        if physical is None:
            return ReadResult(error=f"File '{file_path}': invalid_path")
        result = self.delegate.read(physical, offset, limit)
        return ReadResult(
            error=self._virtualize_text(result.error),
            file_data=result.file_data,
        )

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        physical = self._physical_path(file_path)
        if physical is None:
            return WriteResult(error=f"File '{file_path}': invalid_path")
        result = self.delegate.write(physical, content)
        return WriteResult(
            error=self._virtualize_text(result.error),
            path=file_path if result.path is not None else None,
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await asyncio.to_thread(self.write, file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        physical = self._physical_path(file_path)
        if physical is None:
            return EditResult(error=f"File '{file_path}': invalid_path")
        result = self.delegate.edit(
            physical,
            old_string,
            new_string,
            replace_all=replace_all,
        )
        return EditResult(
            error=self._virtualize_text(result.error),
            path=file_path if result.path is not None else None,
            occurrences=result.occurrences,
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await asyncio.to_thread(
            self.edit,
            file_path,
            old_string,
            new_string,
            replace_all,
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        physical = self._physical_path(path or _VIRTUAL_WORKSPACE)
        if physical is None:
            return GrepResult(error=f"Path '{path}': invalid_path")
        result = self.delegate.grep(pattern, physical, glob)
        matches = (
            [
                {
                    **item,
                    "path": self._virtualize_text(item["path"]) or item["path"],
                }
                for item in result.matches
            ]
            if result.matches is not None
            else None
        )
        return GrepResult(error=self._virtualize_text(result.error), matches=matches)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return await asyncio.to_thread(self.grep, pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        physical = self._physical_path(path or _VIRTUAL_WORKSPACE)
        if physical is None:
            return GlobResult(error=f"Path '{path}': invalid_path")
        result = self.delegate.glob(pattern, physical)
        matches = (
            [self._virtualize_file_info(item) for item in result.matches]
            if result.matches is not None
            else None
        )
        return GlobResult(error=self._virtualize_text(result.error), matches=matches)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await asyncio.to_thread(self.glob, pattern, path)


@lru_cache(maxsize=1)
def get_context_daytona_backend() -> BackendProtocol:
    """Return the shared, context-aware Deep Agents backend instance."""

    return CompositeBackend(
        default=ContextDaytonaSandbox(),
        routes={
            "/skills/": FilesystemBackend(root_dir=_RESOURCE_ROOT / "skills", virtual_mode=True),
            "/memory/": FilesystemBackend(root_dir=_RESOURCE_ROOT / "memory", virtual_mode=True),
            "/memories/user/": StoreBackend(namespace=_user_memory_namespace),
            "/memories/workspace/": StoreBackend(namespace=_thread_memory_namespace),
            "/memos/": StoreBackend(namespace=_memo_namespace),
        },
        artifacts_root="/workspace/artifacts",
    )


class ContextDaytonaSandbox(BaseSandbox):
    """A logical sandbox that connects to Daytona only on first real operation."""

    @staticmethod
    def _context() -> RunContext:
        context = get_runtime(RunContext).context
        if context is None:
            raise RuntimeError("RunContext is required for Daytona workspace isolation")
        return context

    @property
    def id(self) -> str:
        return "context-daytona"

    def _backend(self) -> WorkspaceMappedDaytonaSandbox:
        context = self._context()
        backend = get_daytona_backend_for_thread(
            thread_id=context.thread_id,
            project_id=context.project_id,
            expected_sandbox_id=getattr(context, "expected_sandbox_id", None),
        )
        _persist_sandbox_binding(context, backend.id)
        marker = (context.turn_id, backend.id)
        with _LOCK:
            first_for_run = marker not in _BOUND_EVENTS
            _BOUND_EVENTS.add(marker)
        if first_for_run:
            _emit_custom(
                {
                    "type": "sandbox.bound",
                    "sandbox_id": backend.id,
                    "thread_id": context.thread_id,
                }
            )
        hydration_marker = (
            context.thread_id,
            backend.id,
            tuple(sorted(context.input_asset_ids)),
        )
        with _LOCK:
            should_hydrate = (
                bool(context.input_asset_ids) and hydration_marker not in _HYDRATED_INPUTS
            )
        if should_hydrate:
            assets = _asset_store().require_ready_inputs(
                owner_id=context.owner_id,
                thread_id=context.thread_id,
                asset_ids=context.input_asset_ids,
            )
            uploads: list[tuple[str, bytes]] = []
            for asset in assets:
                _, content = _asset_store().download_bytes(
                    owner_id=context.owner_id,
                    asset_id=asset.id,
                )
                uploads.append(
                    (
                        f"/workspace/input/assets/{asset.id}/{safe_filename(asset.filename)}",
                        content,
                    )
                )
            responses = backend.upload_files(uploads)
            errors = [response.error for response in responses if response.error]
            if errors:
                raise RuntimeError(f"failed to hydrate input assets: {errors[0]}")
            with _LOCK:
                _HYDRATED_INPUTS.add(hydration_marker)
        return backend

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        backend = self._backend()
        before = list_artifact_manifest(backend)
        response = backend.execute(command, timeout=timeout)
        after = list_artifact_manifest(backend)
        for path, item in after.items():
            if before.get(path) != item:
                _publish_artifact(context=self._context(), backend=backend, path=path)
        return response

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        backend = self._backend()
        responses = backend.upload_files(files)
        context = self._context()
        for (path, content), response in zip(files, responses, strict=True):
            if response.error is None and path.startswith(f"{_ARTIFACTS_ROOT}/"):
                _publish_artifact(
                    context=context,
                    backend=backend,
                    path=path,
                    content=content,
                )
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._backend().download_files(paths)

    def ls(self, path: str) -> LsResult:
        return self._backend().ls(path)

    async def als(self, path: str) -> LsResult:
        return await asyncio.to_thread(self.ls, path)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        return self._backend().read(file_path, offset, limit)

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        backend = self._backend()
        result = backend.write(file_path, content)
        if result.error is None and file_path.startswith(f"{_ARTIFACTS_ROOT}/"):
            _publish_artifact(
                context=self._context(),
                backend=backend,
                path=file_path,
                content=content,
            )
        return result

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await asyncio.to_thread(self.write, file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        backend = self._backend()
        result = backend.edit(
            file_path,
            old_string,
            new_string,
            replace_all=replace_all,
        )
        if (
            result.error is None
            and result.occurrences
            and file_path.startswith(f"{_ARTIFACTS_ROOT}/")
        ):
            _publish_artifact(
                context=self._context(),
                backend=backend,
                path=file_path,
            )
        return result

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await asyncio.to_thread(
            self.edit,
            file_path,
            old_string,
            new_string,
            replace_all,
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return self._backend().grep(pattern, path, glob)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return await asyncio.to_thread(self.grep, pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._backend().glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await asyncio.to_thread(self.glob, pattern, path)


def get_daytona_backend_for_thread(
    *,
    thread_id: str,
    project_id: str,
    expected_sandbox_id: str | None = None,
) -> WorkspaceMappedDaytonaSandbox:
    """Resolve a Daytona backend when no Deep Agents ToolRuntime is available."""

    cache_key = (project_id, thread_id)
    with _LOCK:
        if cached := _RESOLVED_BACKENDS.get(cache_key):
            if expected_sandbox_id and cached.id != expected_sandbox_id:
                raise RuntimeError("cached Daytona sandbox does not match the runtime binding")
            return cached

        client = _client()
        labels = {
            "app": "langalpha-next",
            "thread_id": thread_id,
            "project_id": project_id,
        }
        if expected_sandbox_id:
            try:
                existing = client.get(expected_sandbox_id)
            except Exception as exc:
                raise RuntimeError(
                    "bound Daytona sandbox is unavailable; refusing to create an empty workspace"
                ) from exc
            existing_labels = getattr(existing, "labels", {}) or {}
            if any(existing_labels.get(key) != value for key, value in labels.items()):
                raise RuntimeError("bound Daytona sandbox labels do not match workspace")
        else:
            existing = next(iter(client.list(ListSandboxesQuery(labels=labels, limit=1))), None)

        if existing is None:
            settings = get_settings()
            existing = client.create(
                CreateSandboxFromSnapshotParams(
                    language="python",
                    name=_sandbox_name(thread_id),
                    labels=labels,
                    auto_stop_interval=settings.daytona_auto_stop_minutes,
                    auto_archive_interval=settings.daytona_auto_archive_minutes,
                    auto_delete_interval=settings.daytona_auto_delete_minutes,
                    public=False,
                    network_block_all=True,
                ),
                timeout=120,
            )
        else:
            _ensure_started(existing)

        delegate = DaytonaSandbox(
            sandbox=existing,
            timeout=get_settings().max_run_seconds,
        )
        work_dir = str(existing.get_work_dir()).rstrip("/")
        backend = WorkspaceMappedDaytonaSandbox(
            delegate,
            f"{work_dir}/langalpha-workspace",
        )
        created = backend.execute("mkdir -p /workspace", timeout=120)
        if created.exit_code != 0:
            raise RuntimeError("failed to initialize Daytona writable workspace")
        _RESOLVED_BACKENDS[cache_key] = backend
        return backend


def delete_daytona_sandbox(
    *,
    sandbox_id: str,
    thread_id: str,
    project_id: str,
) -> None:
    """Delete the sandbox only after verifying its ownership labels."""

    client = _client()
    sandbox = client.get(sandbox_id)
    expected = {
        "app": "langalpha-next",
        "thread_id": thread_id,
        "project_id": project_id,
    }
    labels = getattr(sandbox, "labels", {}) or {}
    if any(labels.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Daytona sandbox labels do not match the thread")
    client.delete(sandbox, timeout=120, wait=True)
    with _LOCK:
        _RESOLVED_BACKENDS.pop((project_id, thread_id), None)
        _BOUND_EVENTS.difference_update(
            marker for marker in _BOUND_EVENTS if marker[1] == sandbox_id
        )
        _PERSISTED_BINDINGS.discard((thread_id, sandbox_id))
        _HYDRATED_INPUTS.difference_update(
            marker for marker in _HYDRATED_INPUTS if marker[0] == thread_id
        )


def clear_backend_cache() -> None:
    """Test helper; does not delete remote sandboxes."""

    global _CLIENT
    with _LOCK:
        _RESOLVED_BACKENDS.clear()
        _BOUND_EVENTS.clear()
        _PERSISTED_BINDINGS.clear()
        _HYDRATED_INPUTS.clear()
        _asset_store.cache_clear()
        _CLIENT = None
