from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

from langalpha.config import Settings
from langalpha.domain.models import ProjectView, utc_now
from supabase import Client, create_client


class ProjectNotFoundError(LookupError):
    pass


class ProjectStore(Protocol):
    def healthcheck(self) -> None: ...

    def create_project(self, *, owner_id: str, name: str) -> ProjectView: ...

    def get_project(self, *, owner_id: str, project_id: str) -> ProjectView: ...

    def list_projects(self, *, owner_id: str) -> list[ProjectView]: ...

    def rename_project(self, *, owner_id: str, project_id: str, name: str) -> ProjectView: ...

    def bind_sandbox(self, *, owner_id: str, project_id: str, sandbox_id: str) -> ProjectView: ...

    def replace_sandbox(
        self,
        *,
        owner_id: str,
        project_id: str,
        expected_sandbox_id: str,
        sandbox_id: str,
    ) -> ProjectView: ...

    def mark_deleting(self, *, owner_id: str, project_id: str) -> ProjectView: ...

    def delete_project(self, *, owner_id: str, project_id: str) -> None: ...


def _project(row: dict[str, Any]) -> ProjectView:
    return ProjectView.model_validate(row)


class SupabaseProjectStore:
    def __init__(self, settings: Settings, *, client: Client | None = None) -> None:
        self.client = client or create_client(
            settings.require_supabase_url(),
            settings.require_supabase_secret_key(),
        )

    def healthcheck(self) -> None:
        self.client.table("projects").select("id").limit(1).execute()

    @staticmethod
    def _one(rows: object) -> dict[str, Any]:
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ProjectNotFoundError("project not found")
        return rows[0]

    def create_project(self, *, owner_id: str, name: str) -> ProjectView:
        now = utc_now().isoformat()
        response = (
            self.client.table("projects")
            .insert(
                {
                    "id": str(uuid4()),
                    "owner_id": owner_id,
                    "name": name.strip(),
                    "sandbox_id": None,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            .execute()
        )
        return _project(self._one(response.data))

    def get_project(self, *, owner_id: str, project_id: str) -> ProjectView:
        response = (
            self.client.table("projects")
            .select("*")
            .eq("id", project_id)
            .eq("owner_id", owner_id)
            .neq("status", "deleted")
            .limit(1)
            .execute()
        )
        return _project(self._one(response.data))

    def list_projects(self, *, owner_id: str) -> list[ProjectView]:
        response = (
            self.client.table("projects")
            .select("*")
            .eq("owner_id", owner_id)
            .neq("status", "deleted")
            .order("updated_at", desc=True)
            .execute()
        )
        rows = response.data if isinstance(response.data, list) else []
        return [_project(row) for row in rows if isinstance(row, dict)]

    def _update(self, *, owner_id: str, project_id: str, values: dict[str, Any]) -> ProjectView:
        response = (
            self.client.table("projects")
            .update({**values, "updated_at": utc_now().isoformat()})
            .eq("id", project_id)
            .eq("owner_id", owner_id)
            .neq("status", "deleted")
            .execute()
        )
        return _project(self._one(response.data))

    def rename_project(self, *, owner_id: str, project_id: str, name: str) -> ProjectView:
        return self._update(
            owner_id=owner_id,
            project_id=project_id,
            values={"name": name.strip()},
        )

    def bind_sandbox(self, *, owner_id: str, project_id: str, sandbox_id: str) -> ProjectView:
        current = self.get_project(owner_id=owner_id, project_id=project_id)
        if current.sandbox_id is not None and current.sandbox_id != sandbox_id:
            raise RuntimeError("project is already bound to another sandbox")
        if current.sandbox_id == sandbox_id:
            return current
        response = (
            self.client.table("projects")
            .update(
                {
                    "sandbox_id": sandbox_id,
                    "updated_at": utc_now().isoformat(),
                }
            )
            .eq("id", project_id)
            .eq("owner_id", owner_id)
            .is_("sandbox_id", "null")
            .execute()
        )
        if response.data:
            return _project(self._one(response.data))
        current = self.get_project(owner_id=owner_id, project_id=project_id)
        if current.sandbox_id != sandbox_id:
            raise RuntimeError("project sandbox binding changed concurrently")
        return current

    def replace_sandbox(
        self,
        *,
        owner_id: str,
        project_id: str,
        expected_sandbox_id: str,
        sandbox_id: str,
    ) -> ProjectView:
        """Replace a confirmed-missing sandbox without overwriting a concurrent recovery."""

        response = (
            self.client.table("projects")
            .update(
                {
                    "sandbox_id": sandbox_id,
                    "updated_at": utc_now().isoformat(),
                }
            )
            .eq("id", project_id)
            .eq("owner_id", owner_id)
            .eq("status", "active")
            .eq("sandbox_id", expected_sandbox_id)
            .execute()
        )
        if response.data:
            return _project(self._one(response.data))
        current = self.get_project(owner_id=owner_id, project_id=project_id)
        if current.sandbox_id == expected_sandbox_id:
            raise RuntimeError("project sandbox recovery could not update its binding")
        return current

    def mark_deleting(self, *, owner_id: str, project_id: str) -> ProjectView:
        return self._update(
            owner_id=owner_id,
            project_id=project_id,
            values={"status": "deleting"},
        )

    def delete_project(self, *, owner_id: str, project_id: str) -> None:
        self._update(
            owner_id=owner_id,
            project_id=project_id,
            values={"status": "deleted", "sandbox_id": None},
        )
