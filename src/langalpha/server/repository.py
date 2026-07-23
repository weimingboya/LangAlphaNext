from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import aiosqlite

from langalpha.domain.models import (
    Artifact,
    DomainEvent,
    Guidance,
    ProductRun,
    ProductThread,
    RunStatus,
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

CREATE TABLE IF NOT EXISTS product_runs (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES product_threads(id) ON DELETE CASCADE,
    graph_run_id TEXT UNIQUE,
    turn_id TEXT NOT NULL,
    parent_run_id TEXT REFERENCES product_runs(id) ON DELETE SET NULL,
    stream_cursor TEXT,
    status TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_product_runs_active_thread
ON product_runs(thread_id)
WHERE status IN ('pending', 'running');

CREATE TABLE IF NOT EXISTS domain_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    source_event_key TEXT NOT NULL,
    delivery TEXT NOT NULL DEFAULT 'durable',
    project_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    thread_id TEXT NOT NULL REFERENCES product_threads(id) ON DELETE CASCADE,
    turn_id TEXT,
    run_id TEXT REFERENCES product_runs(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_thread_sequence
ON domain_events(thread_id, sequence);

CREATE TABLE IF NOT EXISTS event_outbox (
    event_id TEXT PRIMARY KEY REFERENCES domain_events(id) ON DELETE CASCADE,
    published_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES product_threads(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES product_runs(id) ON DELETE SET NULL,
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
    run_id TEXT NOT NULL REFERENCES product_runs(id) ON DELETE CASCADE,
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


class ActiveRunConflict(RuntimeError):
    """A thread already owns a pending or running product run."""


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    rows = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
    return {str(row[1]) for row in rows}


async def _add_column(
    db: aiosqlite.Connection,
    table: str,
    name: str,
    declaration: str,
) -> None:
    if name not in await _columns(db, table):
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


class Repository:
    """SQLite product projection and runtime binding repository.

    Agent Server remains the execution authority. These tables keep product
    identity, idempotent events, artifacts, and pending delivery records.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            # Earlier local schema revisions are upgraded in place. This is
            # not a reader or migration path for the sibling legacy project.
            await _add_column(db, "product_runs", "turn_id", "TEXT NOT NULL DEFAULT ''")
            await _add_column(db, "product_runs", "parent_run_id", "TEXT")
            await _add_column(db, "product_runs", "stream_cursor", "TEXT")
            await _add_column(
                db,
                "product_runs",
                "cancel_requested",
                "INTEGER NOT NULL DEFAULT 0",
            )
            await _add_column(
                db,
                "domain_events",
                "source_event_key",
                "TEXT NOT NULL DEFAULT ''",
            )
            await _add_column(db, "domain_events", "delivery", "TEXT NOT NULL DEFAULT 'durable'")
            await _add_column(db, "domain_events", "project_id", "TEXT NOT NULL DEFAULT ''")
            await _add_column(db, "domain_events", "workspace_id", "TEXT NOT NULL DEFAULT ''")
            await _add_column(db, "domain_events", "turn_id", "TEXT")
            await _add_column(
                db,
                "domain_events",
                "source",
                'TEXT NOT NULL DEFAULT \'{"agent_id":"main","parent_agent_id":null}\'',
            )
            await _add_column(db, "artifacts", "checksum", "TEXT")
            await _add_column(db, "artifacts", "updated_at", "TEXT NOT NULL DEFAULT ''")
            await db.execute(
                """
                UPDATE domain_events
                SET source_event_key = 'backfill:' || id
                WHERE source_event_key = ''
                """
            )
            await db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_events_source
                ON domain_events(thread_id, source_event_key, type)
                """
            )
            await db.execute(
                """
                UPDATE artifacts
                SET updated_at = created_at
                WHERE updated_at = ''
                """
            )
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
                    thread.created_at.isoformat(),
                    thread.updated_at.isoformat(),
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

    async def create_run(
        self,
        *,
        thread_id: str,
        graph_run_id: str | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
        parent_run_id: str | None = None,
        status: RunStatus = "pending",
    ) -> ProductRun:
        now = utc_now()
        run = ProductRun(
            id=run_id or str(uuid4()),
            thread_id=thread_id,
            graph_run_id=graph_run_id,
            turn_id=turn_id or str(uuid4()),
            parent_run_id=parent_run_id,
            status=status,
            created_at=now,
            updated_at=now,
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            try:
                await db.execute(
                    """
                    INSERT INTO product_runs
                    (id, thread_id, graph_run_id, turn_id, parent_run_id,
                     stream_cursor, status, cancel_requested, error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, NULL, ?, 0, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.thread_id,
                        run.graph_run_id,
                        run.turn_id,
                        run.parent_run_id,
                        run.status,
                        run.error,
                        run.created_at.isoformat(),
                        run.updated_at.isoformat(),
                    ),
                )
                await db.execute(
                    "UPDATE product_threads SET updated_at = ? WHERE id = ?",
                    (now.isoformat(), thread_id),
                )
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                if run.status in {"pending", "running"} and "product_runs.thread_id" in str(exc):
                    raise ActiveRunConflict(
                        f"thread already has an active run: {thread_id}"
                    ) from exc
                raise
        return run

    async def attach_runtime_run(self, run_id: str, graph_run_id: str) -> ProductRun:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE product_runs
                SET graph_run_id = ?, updated_at = ?
                WHERE id = ? AND graph_run_id IS NULL
                """,
                (graph_run_id, utc_now().isoformat(), run_id),
            )
            await db.commit()
        run = await self.get_run(run_id)
        if run is None:
            raise KeyError(f"product run not found: {run_id}")
        if run.graph_run_id != graph_run_id:
            raise RuntimeError("product run is already bound to another runtime run")
        return run

    async def get_run(self, run_id: str) -> ProductRun | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute("SELECT * FROM product_runs WHERE id = ?", (run_id,))
            ).fetchone()
        return self._run(row) if row else None

    async def list_active_runs(self) -> list[ProductRun]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT * FROM product_runs
                    WHERE status IN ('pending', 'running') AND graph_run_id IS NOT NULL
                    ORDER BY created_at
                    """
                )
            ).fetchall()
        return [self._run(row) for row in rows]

    async def list_open_runs(self, thread_id: str) -> list[ProductRun]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT * FROM product_runs
                    WHERE thread_id = ?
                      AND status IN ('pending', 'running', 'interrupted')
                    ORDER BY created_at
                    """,
                    (thread_id,),
                )
            ).fetchall()
        return [self._run(row) for row in rows]

    async def update_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE product_runs
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, utc_now().isoformat(), run_id),
            )
            await db.commit()

    async def set_cancel_requested(self, run_id: str, requested: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE product_runs
                SET cancel_requested = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(requested), utc_now().isoformat(), run_id),
            )
            await db.commit()

    async def get_run_cursor(self, run_id: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute("SELECT stream_cursor FROM product_runs WHERE id = ?", (run_id,))
            ).fetchone()
        return str(row[0]) if row and row[0] else None

    async def set_run_cursor(self, run_id: str, cursor: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE product_runs
                SET stream_cursor = ?, updated_at = ?
                WHERE id = ?
                """,
                (cursor, utc_now().isoformat(), run_id),
            )
            await db.commit()

    async def append_event(
        self,
        *,
        thread_id: str,
        run_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        source_event_key: str | None = None,
        source: dict[str, str | None] | None = None,
    ) -> DomainEvent:
        """Insert one durable event and its outbox row atomically.

        Repeated runtime events return the first stored event instead of
        allocating a new sequence.
        """

        binding = await self.get_binding(thread_id)
        if binding is None:
            raise KeyError(f"runtime binding not found: {thread_id}")
        run = await self.get_run(run_id) if run_id else None
        turn_id = run.turn_id if run else payload.get("turn_id")
        key = source_event_key or f"product:{uuid4()}"
        event_id = str(uuid5(NAMESPACE_URL, f"{thread_id}:{key}:{event_type}"))
        created_at = utc_now()
        event_source = source or {"agent_id": "main", "parent_agent_id": None}
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN")
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO domain_events
                (id, source_event_key, delivery, project_id, workspace_id,
                 thread_id, turn_id, run_id, type, source, payload, created_at)
                VALUES (?, ?, 'durable', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    key,
                    binding.project_id,
                    binding.workspace_id,
                    thread_id,
                    turn_id,
                    run_id,
                    event_type,
                    json.dumps(event_source, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    created_at.isoformat(),
                ),
            )
            if cursor.rowcount == 1:
                await db.execute(
                    """
                    INSERT INTO event_outbox (event_id, created_at)
                    VALUES (?, ?)
                    """,
                    (event_id, created_at.isoformat()),
                )
            row = await (
                await db.execute("SELECT * FROM domain_events WHERE id = ?", (event_id,))
            ).fetchone()
            await db.commit()
        assert row is not None
        return self._event(row)

    async def list_events(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[DomainEvent]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT * FROM domain_events
                    WHERE thread_id = ? AND sequence > ?
                    ORDER BY sequence
                    LIMIT ?
                    """,
                    (thread_id, after_sequence, limit),
                )
            ).fetchall()
        return [self._event(row) for row in rows]

    async def run_estimated_cost(self, run_id: str) -> float:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    """
                    SELECT COALESCE(
                        SUM(CAST(json_extract(payload, '$.estimated_cost_usd') AS REAL)),
                        0
                    )
                    FROM domain_events
                    WHERE run_id = ? AND type = 'usage.updated'
                    """,
                    (run_id,),
                )
            ).fetchone()
        return float(row[0]) if row else 0.0

    async def pending_outbox_count(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute("SELECT COUNT(*) FROM event_outbox WHERE published_at IS NULL")
            ).fetchone()
        return int(row[0]) if row else 0

    async def list_pending_outbox(self, *, limit: int = 100) -> list[DomainEvent]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT events.*
                    FROM event_outbox AS outbox
                    JOIN domain_events AS events ON events.id = outbox.event_id
                    WHERE outbox.published_at IS NULL
                    ORDER BY outbox.created_at, outbox.event_id
                    LIMIT ?
                    """,
                    (max(1, min(limit, 1_000)),),
                )
            ).fetchall()
        return [self._event(row) for row in rows]

    async def mark_outbox_attempt(self, event_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE event_outbox
                SET attempts = attempts + 1
                WHERE event_id = ? AND published_at IS NULL
                """,
                (event_id,),
            )
            await db.commit()

    async def mark_outbox_published(self, event_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE event_outbox
                SET published_at = ?
                WHERE event_id = ? AND published_at IS NULL
                """,
                (utc_now().isoformat(), event_id),
            )
            await db.commit()

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
                    run_id = excluded.run_id,
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

    async def create_artifact(self, **kwargs: Any) -> Artifact:
        return await self.upsert_artifact(**kwargs)

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
                    guidance.thread_id,
                    guidance.run_id,
                    guidance.message,
                    guidance.status,
                    guidance.created_at.isoformat(),
                    guidance.updated_at.isoformat(),
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
                    WITH RECURSIVE ancestors(id) AS (
                        SELECT ?
                        UNION ALL
                        SELECT runs.parent_run_id
                        FROM product_runs AS runs
                        JOIN ancestors ON runs.id = ancestors.id
                        WHERE runs.parent_run_id IS NOT NULL
                    )
                    SELECT guidance.*
                    FROM guidance
                    JOIN ancestors ON guidance.run_id = ancestors.id
                    WHERE
                        (guidance.run_id = ? AND guidance.status = 'accepted')
                        OR
                        (guidance.run_id != ? AND guidance.status = 'reclaimed')
                    ORDER BY guidance.created_at
                    """,
                    (run_id, run_id, run_id),
                )
            ).fetchall()
            if rows:
                await db.executemany(
                    """
                    UPDATE guidance
                    SET run_id = ?, status = 'delivered', updated_at = ?
                    WHERE id = ? AND status IN ('accepted', 'reclaimed')
                    """,
                    [(run_id, now, row["id"]) for row in rows],
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

    async def reclaim_guidance(self, run_id: str) -> list[Guidance]:
        now = utc_now().isoformat()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            rows = await (
                await db.execute(
                    """
                    SELECT * FROM guidance
                    WHERE run_id = ? AND status IN ('accepted', 'delivered')
                    ORDER BY created_at
                    """,
                    (run_id,),
                )
            ).fetchall()
            await db.execute(
                """
                UPDATE guidance
                SET status = 'reclaimed', updated_at = ?
                WHERE run_id = ? AND status IN ('accepted', 'delivered')
                """,
                (now, run_id),
            )
            await db.commit()
        return [
            Guidance(
                id=row["id"],
                thread_id=row["thread_id"],
                run_id=row["run_id"],
                message=row["message"],
                status="reclaimed",
                created_at=_dt(row["created_at"]),
                updated_at=_dt(now),
            )
            for row in rows
        ]

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
    def _run(row: Any) -> ProductRun:
        return ProductRun(
            id=row["id"],
            thread_id=row["thread_id"],
            graph_run_id=row["graph_run_id"],
            turn_id=row["turn_id"],
            parent_run_id=row["parent_run_id"],
            status=row["status"],
            cancel_requested=bool(row["cancel_requested"]),
            error=row["error"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    @staticmethod
    def _event(row: Any) -> DomainEvent:
        return DomainEvent(
            sequence=row["sequence"],
            id=row["id"],
            source_event_key=row["source_event_key"],
            delivery=row["delivery"],
            project_id=row["project_id"],
            workspace_id=row["workspace_id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            run_id=row["run_id"],
            type=row["type"],
            source=json.loads(row["source"]),
            payload=json.loads(row["payload"]),
            created_at=_dt(row["created_at"]),
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


def event_types(events: Iterable[DomainEvent]) -> list[str]:
    return [event.type for event in events]
