from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import aiosqlite

from langalpha.domain.models import (
    Artifact,
    Guidance,
    ProductThread,
    RuntimeBinding,
    utc_now,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS product_threads (
    id TEXT PRIMARY KEY,
    graph_thread_id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_bindings (
    product_thread_id TEXT PRIMARY KEY
        REFERENCES product_threads(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL UNIQUE,
    runtime_thread_id TEXT NOT NULL UNIQUE,
    assistant_id TEXT NOT NULL,
    kernel_version TEXT NOT NULL,
    profile TEXT NOT NULL,
    sandbox_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES product_threads(id) ON DELETE CASCADE,
    run_id TEXT,
    name TEXT NOT NULL,
    sandbox_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    checksum TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_path
ON artifacts(thread_id, sandbox_path);

CREATE TABLE IF NOT EXISTS guidance (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES product_threads(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_guidance_run_status
ON guidance(run_id, status, created_at);
"""


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Repository:
    """Small product repository; Agent Server owns every runtime resource."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            existing = {
                str(row[0])
                for row in await (
                    await db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                ).fetchall()
            }
            legacy = existing & {"product_runs", "domain_events", "event_outbox"}
            if legacy:
                names = ", ".join(sorted(legacy))
                raise RuntimeError(
                    f"obsolete runtime projection tables found ({names}); "
                    "use a fresh LANGALPHA_DATABASE_PATH"
                )
            await db.executescript(SCHEMA)
            await db.commit()

    async def create_thread(
        self,
        *,
        graph_thread_id: str,
        workspace_id: str,
        title: str,
        thread_id: str | None = None,
        project_id: str = "langalpha-local",
        owner_id: str = "local-user",
        assistant_id: str = "main",
        kernel_version: str = "0.1.0",
        profile: str = "main",
    ) -> ProductThread:
        now = utc_now()
        thread = ProductThread(
            id=thread_id or str(uuid4()),
            graph_thread_id=graph_thread_id,
            workspace_id=workspace_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN")
            await db.execute(
                """
                INSERT INTO product_threads
                (id, graph_thread_id, workspace_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread.id,
                    thread.graph_thread_id,
                    thread.workspace_id,
                    thread.title,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            await db.execute(
                """
                INSERT INTO runtime_bindings
                (product_thread_id, project_id, owner_id, workspace_id,
                 runtime_thread_id, assistant_id, kernel_version, profile,
                 sandbox_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    thread.id,
                    project_id,
                    owner_id,
                    workspace_id,
                    graph_thread_id,
                    assistant_id,
                    kernel_version,
                    profile,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            await db.commit()
        return thread

    async def get_thread(self, thread_id: str) -> ProductThread | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute("SELECT * FROM product_threads WHERE id = ?", (thread_id,))
            ).fetchone()
        return self._thread(row) if row else None

    async def list_threads(self) -> list[ProductThread]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute("SELECT * FROM product_threads ORDER BY updated_at DESC")
            ).fetchall()
        return [self._thread(row) for row in rows]

    async def touch_thread(self, thread_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE product_threads SET updated_at = ? WHERE id = ?",
                (utc_now().isoformat(), thread_id),
            )
            await db.commit()

    async def get_binding(self, thread_id: str) -> RuntimeBinding | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM runtime_bindings WHERE product_thread_id = ?",
                    (thread_id,),
                )
            ).fetchone()
        return self._binding(row) if row else None

    async def bind_sandbox(self, thread_id: str, sandbox_id: str) -> RuntimeBinding:
        now = utc_now().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE runtime_bindings
                SET sandbox_id = ?, updated_at = ?
                WHERE product_thread_id = ?
                  AND (sandbox_id IS NULL OR sandbox_id = ?)
                """,
                (sandbox_id, now, thread_id, sandbox_id),
            )
            await db.commit()
        binding = await self.get_binding(thread_id)
        if binding is None:
            raise KeyError(f"runtime binding not found: {thread_id}")
        if binding.sandbox_id != sandbox_id:
            raise RuntimeError("workspace is already bound to a different Daytona sandbox")
        return binding

    async def upsert_artifact(
        self,
        *,
        thread_id: str,
        run_id: str | None,
        name: str,
        sandbox_path: str,
        media_type: str,
        size_bytes: int,
        checksum: str | None = None,
    ) -> Artifact:
        artifact_id = str(uuid5(NAMESPACE_URL, f"{thread_id}:{sandbox_path}"))
        now = utc_now()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                INSERT INTO artifacts
                (id, thread_id, run_id, name, sandbox_path, media_type,
                 size_bytes, checksum, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id, sandbox_path) DO UPDATE SET
                    run_id = COALESCE(excluded.run_id, artifacts.run_id),
                    name = excluded.name,
                    media_type = excluded.media_type,
                    size_bytes = excluded.size_bytes,
                    checksum = COALESCE(excluded.checksum, artifacts.checksum),
                    updated_at = excluded.updated_at
                """,
                (
                    artifact_id,
                    thread_id,
                    run_id,
                    name,
                    sandbox_path,
                    media_type,
                    size_bytes,
                    checksum,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM artifacts WHERE thread_id = ? AND sandbox_path = ?",
                    (thread_id, sandbox_path),
                )
            ).fetchone()
            await db.commit()
        assert row is not None
        return self._artifact(row)

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
            ).fetchone()
        return self._artifact(row) if row else None

    async def list_artifacts(self, thread_id: str) -> list[Artifact]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE thread_id = ?
                    ORDER BY updated_at DESC
                    """,
                    (thread_id,),
                )
            ).fetchall()
        return [self._artifact(row) for row in rows]

    async def create_guidance(self, *, thread_id: str, run_id: str, message: str) -> Guidance:
        now = utc_now()
        guidance = Guidance(
            id=str(uuid4()),
            thread_id=thread_id,
            run_id=run_id,
            message=message,
            status="accepted",
            created_at=now,
            updated_at=now,
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO guidance
                (id, thread_id, run_id, message, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guidance.id,
                    thread_id,
                    run_id,
                    message,
                    guidance.status,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            await db.commit()
        return guidance

    async def list_guidance(self, run_id: str, *, status: str | None = None) -> list[Guidance]:
        sql = "SELECT * FROM guidance WHERE run_id = ?"
        params: list[Any] = [run_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(sql, params)).fetchall()
        return [self._guidance(row) for row in rows]

    async def claim_guidance(self, run_id: str) -> list[Guidance]:
        now = utc_now().isoformat()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            rows = await (
                await db.execute(
                    """
                    SELECT * FROM guidance
                    WHERE run_id = ? AND status IN ('accepted', 'reclaimed')
                    ORDER BY created_at
                    """,
                    (run_id,),
                )
            ).fetchall()
            if rows:
                await db.executemany(
                    """
                    UPDATE guidance
                    SET status = 'delivered', updated_at = ?
                    WHERE id = ?
                    """,
                    [(now, row["id"]) for row in rows],
                )
            await db.commit()
        return [
            Guidance(
                id=row["id"],
                thread_id=row["thread_id"],
                run_id=run_id,
                message=row["message"],
                status="delivered",
                created_at=_dt(row["created_at"]),
                updated_at=_dt(now),
            )
            for row in rows
        ]

    async def return_guidance(self, run_id: str, ids: list[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"""
                UPDATE guidance
                SET status = 'returned', updated_at = ?
                WHERE run_id = ? AND status = 'delivered'
                  AND id IN ({placeholders})
                """,
                [utc_now().isoformat(), run_id, *ids],
            )
            await db.commit()

    async def transfer_open_guidance(self, source_run_id: str, target_run_id: str) -> None:
        """Move undelivered guidance to a successor control ID on HITL resume."""

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE guidance
                SET run_id = ?, status = 'accepted', updated_at = ?
                WHERE run_id = ? AND status IN ('accepted', 'delivered', 'reclaimed')
                """,
                (target_run_id, utc_now().isoformat(), source_run_id),
            )
            await db.commit()

    @staticmethod
    def _thread(row: Any) -> ProductThread:
        return ProductThread(
            id=row["id"],
            graph_thread_id=row["graph_thread_id"],
            workspace_id=row["workspace_id"],
            title=row["title"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    @staticmethod
    def _binding(row: Any) -> RuntimeBinding:
        return RuntimeBinding(
            project_id=row["project_id"],
            owner_id=row["owner_id"],
            workspace_id=row["workspace_id"],
            product_thread_id=row["product_thread_id"],
            runtime_thread_id=row["runtime_thread_id"],
            assistant_id=row["assistant_id"],
            kernel_version=row["kernel_version"],
            profile=row["profile"],
            sandbox_id=row["sandbox_id"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    @staticmethod
    def _artifact(row: Any) -> Artifact:
        return Artifact(
            id=row["id"],
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            name=row["name"],
            sandbox_path=row["sandbox_path"],
            media_type=row["media_type"],
            size_bytes=row["size_bytes"],
            checksum=row["checksum"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    @staticmethod
    def _guidance(row: Any) -> Guidance:
        return Guidance(
            id=row["id"],
            thread_id=row["thread_id"],
            run_id=row["run_id"],
            message=row["message"],
            status=row["status"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )
