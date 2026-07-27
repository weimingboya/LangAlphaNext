from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from deepagents.backends import StoreBackend
from deepagents.backends.protocol import (
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from langalpha.agent.context import RunContext
from langalpha.backends import daytona as module
from langalpha.config import Settings


def test_workspace_adapter_maps_virtual_paths_to_daytona_work_dir() -> None:
    calls: list[object] = []

    class Delegate:
        id = "sandbox-id"

        def execute(self, command: str, *, timeout: int | None = None):
            calls.append(("execute", command, timeout))
            return SimpleNamespace(output="ok", exit_code=0)

        def upload_files(self, files: list[tuple[str, bytes]]):
            calls.append(("upload", files))
            return [FileUploadResponse(path=path, error=None) for path, _ in files]

        def download_files(self, paths: list[str]):
            calls.append(("download", paths))
            return [
                FileDownloadResponse(path=path, content=b"result", error=None) for path in paths
            ]

    backend = module.WorkspaceMappedDaytonaSandbox(
        Delegate(),  # type: ignore[arg-type]
        "/home/daytona/langalpha-workspace",
    )
    executed = backend.execute(
        'python -c "from pathlib import Path; '
        "print(Path('/workspace/input/data.csv').read_text())\"",
        timeout=30,
    )
    uploaded = backend.upload_files(
        [
            ("/workspace/input/data.csv", b"value\n1\n"),
            ("/etc/blocked", b"blocked"),
        ]
    )
    downloaded = backend.download_files(["/workspace/artifacts/report.md", "/etc/blocked"])

    assert executed.exit_code == 0
    assert calls[0] == (
        "execute",
        'python -c "from pathlib import Path; '
        "print(Path('/home/daytona/langalpha-workspace/input/data.csv').read_text())\"",
        30,
    )
    assert calls[1] == (
        "upload",
        [
            (
                "/home/daytona/langalpha-workspace/input/data.csv",
                b"value\n1\n",
            )
        ],
    )
    assert calls[2] == (
        "download",
        ["/home/daytona/langalpha-workspace/artifacts/report.md"],
    )
    assert uploaded[0].path == "/workspace/input/data.csv"
    assert uploaded[0].error is None
    assert uploaded[1].error == "invalid_path"
    assert downloaded[0].path == "/workspace/artifacts/report.md"
    assert downloaded[0].content == b"result"
    assert downloaded[1].error == "invalid_path"


def test_workspace_adapter_maps_deep_agents_file_api_before_command_encoding() -> None:
    physical_root = "/home/daytona/langalpha-workspace"
    calls: list[tuple[object, ...]] = []

    class Delegate:
        id = "sandbox-id"

        def ls(self, path: str):
            calls.append(("ls", path))
            return LsResult(entries=[{"path": f"{path}/uploads", "is_dir": True}])

        def read(self, path: str, offset: int, limit: int):
            calls.append(("read", path, offset, limit))
            return ReadResult(file_data={"content": "value\n1", "encoding": "utf-8"})

        def write(self, path: str, content: str):
            calls.append(("write", path, content))
            return WriteResult(path=path)

        def edit(
            self,
            path: str,
            old_string: str,
            new_string: str,
            *,
            replace_all: bool,
        ):
            calls.append(("edit", path, old_string, new_string, replace_all))
            return EditResult(path=path, occurrences=1)

        def grep(self, pattern: str, path: str, glob: str | None):
            calls.append(("grep", pattern, path, glob))
            return GrepResult(matches=[{"path": f"{path}/data.csv", "line": 1, "text": "value"}])

        def glob(self, pattern: str, path: str):
            calls.append(("glob", pattern, path))
            return GlobResult(matches=[{"path": f"{path}/data.csv", "is_dir": False}])

    backend = module.WorkspaceMappedDaytonaSandbox(
        Delegate(),  # type: ignore[arg-type]
        physical_root,
    )

    assert backend.ls("/workspace").entries == [{"path": "/workspace/uploads", "is_dir": True}]
    assert backend.read("/workspace/uploads/data.csv").error is None
    assert backend.write("/workspace/artifacts/report.md", "report").path == (
        "/workspace/artifacts/report.md"
    )
    assert (
        backend.edit(
            "/workspace/artifacts/report.md",
            "report",
            "updated",
        ).path
        == "/workspace/artifacts/report.md"
    )
    assert backend.grep("value", "/workspace/uploads").matches == [
        {"path": "/workspace/uploads/data.csv", "line": 1, "text": "value"}
    ]
    assert backend.glob("**/*.csv", "/workspace").matches == [
        {"path": "/workspace/data.csv", "is_dir": False}
    ]
    assert calls == [
        ("ls", physical_root),
        ("read", f"{physical_root}/uploads/data.csv", 0, 2000),
        ("write", f"{physical_root}/artifacts/report.md", "report"),
        (
            "edit",
            f"{physical_root}/artifacts/report.md",
            "report",
            "updated",
            False,
        ),
        ("grep", "value", f"{physical_root}/uploads", None),
        ("glob", "**/*.csv", physical_root),
    ]


def test_artifact_manifest_virtualizes_daytona_physical_paths() -> None:
    physical_root = "/home/daytona/langalpha-workspace"

    class Delegate:
        id = "sandbox-id"

        def execute(self, command: str, *, timeout: int | None = None):
            assert physical_root in command
            assert timeout == 120
            return SimpleNamespace(
                output=json.dumps(
                    {
                        "path": f"{physical_root}/artifacts/report.md",
                        "size_bytes": 6,
                        "checksum": hashlib.sha256(b"report").hexdigest(),
                    }
                ),
                exit_code=0,
            )

    backend = module.WorkspaceMappedDaytonaSandbox(
        Delegate(),  # type: ignore[arg-type]
        physical_root,
    )
    manifest = module.list_artifact_manifest(backend)

    assert set(manifest) == {"/workspace/artifacts/report.md"}
    assert manifest["/workspace/artifacts/report.md"]["size_bytes"] == 6


def test_builtin_skills_do_not_connect_to_daytona(monkeypatch) -> None:
    def forbidden(**_: str):
        raise AssertionError("Daytona must stay lazy while product assets are read")

    monkeypatch.setattr(module, "get_daytona_backend_for_thread", forbidden)
    module.get_context_daytona_backend.cache_clear()
    backend = module.get_context_daytona_backend()

    listing = backend.ls("/skills/")
    assert listing.error is None
    assert any(item["path"].endswith("financial-research/") for item in listing.entries or [])

    skill = backend.read("/skills/financial-research/SKILL.md")
    assert skill.error is None
    assert "Financial research" in skill.file_data["content"]


def test_persistent_store_routes_are_scoped_by_user_and_thread() -> None:
    module.get_context_daytona_backend.cache_clear()
    backend = module.get_context_daytona_backend()
    assert isinstance(backend.routes["/memories/user/"], StoreBackend)
    assert isinstance(backend.routes["/memories/workspace/"], StoreBackend)
    assert isinstance(backend.routes["/memos/"], StoreBackend)

    context = RunContext(
        project_id="project",
        owner_id="owner",
        thread_id="thread",
        turn_id="turn",
    )
    runtime = SimpleNamespace(context=context)
    assert module._user_memory_namespace(runtime) == ("project", "owner", "memory")
    assert module._thread_memory_namespace(runtime) == (
        "project",
        "owner",
        "threads",
        "thread",
        "memory",
    )
    assert module._memo_namespace(runtime) == ("project", "owner", "memos")


def test_lazy_backend_resolves_only_on_operation(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class Resolved:
        id = "sandbox-id"

        def execute(self, command: str, *, timeout: int | None = None):
            calls.append((command, str(timeout)))
            return SimpleNamespace(output="", exit_code=0)

    def resolve(
        *,
        thread_id: str,
        project_id: str,
        expected_sandbox_id: str | None = None,
    ):
        assert thread_id == "thread"
        assert project_id == "project"
        assert expected_sandbox_id is None
        return Resolved()

    monkeypatch.setattr(module, "get_daytona_backend_for_thread", resolve)
    monkeypatch.setattr(
        module.ContextDaytonaSandbox,
        "_context",
        staticmethod(
            lambda: SimpleNamespace(
                thread_id="thread",
                project_id="project",
                turn_id="turn",
                input_asset_ids=(),
            )
        ),
    )
    lazy = module.ContextDaytonaSandbox()

    assert lazy.id == "context-daytona"
    assert calls == []
    response = lazy.execute("python -V", timeout=10)
    assert response.exit_code == 0
    assert calls == [
        (module._MANIFEST_COMMAND, "120"),
        ("python -V", "10"),
        (module._MANIFEST_COMMAND, "120"),
    ]


def test_execute_and_upload_emit_binding_and_asset_events(monkeypatch) -> None:
    emitted: list[dict[str, object]] = []

    class Resolved:
        id = "sandbox-id"
        artifact_exists = False

        def execute(self, command: str, *, timeout: int | None = None):
            if command == module._MANIFEST_COMMAND:
                output = (
                    json.dumps(
                        {
                            "path": "/workspace/artifacts/report.md",
                            "size_bytes": 6,
                            "checksum": hashlib.sha256(b"report").hexdigest(),
                        }
                    )
                    if self.artifact_exists
                    else ""
                )
                return SimpleNamespace(output=output, exit_code=0)
            self.artifact_exists = True
            return SimpleNamespace(output="created", exit_code=0)

        def upload_files(self, files: list[tuple[str, bytes]]):
            return [SimpleNamespace(path=path, error=None) for path, _content in files]

        def write(self, path: str, content: str):
            return WriteResult(path=path)

        def download_files(self, paths: list[str]):
            return [SimpleNamespace(path=path, content=b"report", error=None) for path in paths]

    context = RunContext(
        project_id="project",
        owner_id="owner",
        thread_id="thread",
        turn_id="turn",
    )
    resolved = Resolved()
    published: list[dict[str, object]] = []

    class Store:
        def publish_artifact(self, **kwargs: object):
            published.append(kwargs)
            return SimpleNamespace(
                model_dump=lambda **_: {
                    "id": f"asset-{len(published)}",
                    "sandbox_path": kwargs["sandbox_path"],
                }
            )

    module.clear_backend_cache()
    monkeypatch.setattr(
        module,
        "get_daytona_backend_for_thread",
        lambda **_: resolved,
    )
    monkeypatch.setattr(module, "_asset_store", lambda: Store())
    monkeypatch.setattr(
        module.ContextDaytonaSandbox,
        "_context",
        staticmethod(lambda: context),
    )
    monkeypatch.setattr(module, "_emit_custom", emitted.append)
    lazy = module.ContextDaytonaSandbox()

    response = lazy.execute("write report")
    assert response.exit_code == 0
    assert emitted[0] == {
        "type": "sandbox.bound",
        "sandbox_id": "sandbox-id",
        "thread_id": "thread",
    }
    assert emitted[1]["type"] == "asset.ready"
    assert emitted[1]["sandbox_path"] == "/workspace/artifacts/report.md"

    lazy.upload_files([("/workspace/artifacts/chart.svg", b"<svg/>")])
    assert [event["type"] for event in emitted].count("sandbox.bound") == 1
    assert emitted[-1]["sandbox_path"] == "/workspace/artifacts/chart.svg"
    assert published[-1]["content"] == b"<svg/>"

    lazy.write("/workspace/artifacts/summary.md", "summary")
    assert emitted[-1]["sandbox_path"] == "/workspace/artifacts/summary.md"
    assert published[-1]["content"] == b"summary"


def test_daytona_creation_is_private_blocked_and_binding_is_strict(monkeypatch) -> None:
    created: list[object] = []
    sandbox = SimpleNamespace(
        id="sandbox-id",
        state="started",
        get_work_dir=lambda: "/home/daytona",
        labels={
            "app": "langalpha-next",
            "thread_id": "thread",
            "project_id": "project",
        },
    )

    class Client:
        def list(self, *_: object, **__: object):
            return iter(())

        def create(self, params: object, **_: object):
            created.append(params)
            return sandbox

        def get(self, sandbox_id: str):
            if sandbox_id == "missing":
                raise KeyError(sandbox_id)
            return sandbox

    class Delegate:
        id = "sandbox-id"

        def execute(self, command: str, *, timeout: int | None = None):
            assert command == "mkdir -p /home/daytona/langalpha-workspace"
            assert timeout == 120
            return SimpleNamespace(output="", exit_code=0)

    monkeypatch.setattr(module, "_client", lambda: Client())
    monkeypatch.setattr(module, "DaytonaSandbox", lambda **_: Delegate())
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            OPENAI_API_KEY="test",
            DAYTONA_API_KEY="test",
        ),
    )
    module.clear_backend_cache()

    backend = module.get_daytona_backend_for_thread(
        thread_id="thread",
        project_id="project",
    )
    assert backend.id == "sandbox-id"
    params = created[0]
    assert params.public is False
    assert params.network_block_all is True
    assert params.auto_stop_interval == 60
    assert params.auto_archive_interval == 10_080
    assert params.auto_delete_interval == 43_200

    module.clear_backend_cache()
    with pytest.raises(RuntimeError, match="refusing to create"):
        module.get_daytona_backend_for_thread(
            thread_id="thread",
            project_id="project",
            expected_sandbox_id="missing",
        )
