from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from langalpha.config import Settings
from langalpha.domain.models import (
    Asset,
    AssetDownloadTicket,
    AssetUploadCreate,
    AssetUploadTicket,
    utc_now,
)
from supabase import Client, create_client


class AssetNotFoundError(LookupError):
    pass


class AssetValidationError(ValueError):
    pass


class AssetStore(Protocol):
    def create_upload(
        self,
        *,
        owner_id: str,
        thread_id: str,
        request: AssetUploadCreate,
    ) -> AssetUploadTicket: ...

    def complete_upload(
        self,
        *,
        owner_id: str,
        asset_id: str,
        sha256: str,
    ) -> Asset: ...

    def get_asset(self, *, owner_id: str, asset_id: str) -> Asset: ...

    def list_assets(self, *, owner_id: str, thread_id: str) -> list[Asset]: ...

    def download_ticket(
        self,
        *,
        owner_id: str,
        asset_id: str,
        expires_in: int = 300,
    ) -> AssetDownloadTicket: ...

    def download_bytes(self, *, owner_id: str, asset_id: str) -> tuple[Asset, bytes]: ...

    def delete_asset(self, *, owner_id: str, asset_id: str) -> None: ...

    def publish_artifact(
        self,
        *,
        owner_id: str,
        thread_id: str,
        turn_id: str,
        sandbox_path: str,
        content: bytes,
        media_type: str | None = None,
    ) -> Asset: ...

    def require_ready_inputs(
        self,
        *,
        owner_id: str,
        thread_id: str,
        asset_ids: Sequence[str],
    ) -> list[Asset]: ...


def safe_filename(value: str) -> str:
    name = PurePosixPath(value).name
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip(".-")
    if not name:
        raise AssetValidationError("invalid filename")
    return name[:160]


def _asset(row: dict[str, Any]) -> Asset:
    return Asset.model_validate(row)


class SupabaseAssetStore:
    """Small server-only adapter for the product asset registry and private bucket."""

    def __init__(self, settings: Settings, *, client: Client | None = None) -> None:
        self.url = settings.require_supabase_url()
        self.bucket_id = settings.supabase_storage_bucket
        self.client = client or create_client(
            self.url,
            settings.require_supabase_secret_key(),
        )

    @property
    def bucket(self):
        return self.client.storage.from_(self.bucket_id)

    def _tus_endpoint(self) -> str:
        parsed = urlparse(self.url)
        hostname = parsed.hostname or ""
        if hostname.endswith(".supabase.co"):
            project_ref = hostname.removesuffix(".supabase.co")
            return (
                f"{parsed.scheme}://{project_ref}.storage.supabase.co/storage/v1/upload/resumable"
            )
        return f"{self.url}/storage/v1/upload/resumable"

    def _one(self, rows: object) -> dict[str, Any]:
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise AssetNotFoundError("asset not found")
        return rows[0]

    def _insert(self, values: dict[str, Any]) -> Asset:
        response = self.client.table("assets").insert(values).execute()
        return _asset(self._one(response.data))

    def _update(self, asset_id: str, values: dict[str, Any]) -> Asset:
        response = (
            self.client.table("assets")
            .update({**values, "updated_at": utc_now().isoformat()})
            .eq("id", asset_id)
            .execute()
        )
        return _asset(self._one(response.data))

    def _by_logical_key(self, thread_id: str, logical_key: str) -> Asset | None:
        response = (
            self.client.table("assets")
            .select("*")
            .eq("thread_id", thread_id)
            .eq("logical_key", logical_key)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        return _asset(self._one(response.data))

    def create_upload(
        self,
        *,
        owner_id: str,
        thread_id: str,
        request: AssetUploadCreate,
    ) -> AssetUploadTicket:
        asset_id = str(uuid4())
        filename = safe_filename(request.filename)
        object_path = f"{owner_id}/{thread_id}/{asset_id}/{request.sha256}/{filename}"
        now = utc_now().isoformat()
        asset = self._insert(
            {
                "id": asset_id,
                "owner_id": owner_id,
                "thread_id": thread_id,
                "turn_id": None,
                "role": "input",
                "status": "uploading",
                "logical_key": f"input:{asset_id}",
                "bucket_id": self.bucket_id,
                "object_path": object_path,
                "sandbox_path": None,
                "filename": filename,
                "media_type": request.media_type,
                "size_bytes": request.size_bytes,
                "sha256": request.sha256,
                "retention_class": "standard",
                "created_at": now,
                "updated_at": now,
            }
        )
        try:
            signed = self.bucket.create_signed_upload_url(object_path)
        except Exception:
            self._update(asset_id, {"status": "failed"})
            raise
        return AssetUploadTicket(
            asset=asset,
            signed_url=str(signed["signed_url"]),
            token=str(signed["token"]),
            tus_endpoint=self._tus_endpoint(),
        )

    def complete_upload(
        self,
        *,
        owner_id: str,
        asset_id: str,
        sha256: str,
    ) -> Asset:
        asset = self.get_asset(owner_id=owner_id, asset_id=asset_id)
        if asset.status != "uploading":
            if asset.status == "ready" and asset.sha256 == sha256:
                return asset
            raise AssetValidationError("asset is not awaiting upload completion")
        if asset.sha256 != sha256:
            raise AssetValidationError("upload checksum does not match ticket")
        content = self.bucket.download(asset.object_path)
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != sha256:
            self._update(asset.id, {"status": "failed"})
            raise AssetValidationError("uploaded object checksum mismatch")
        if asset.size_bytes != len(content):
            self._update(asset.id, {"status": "failed"})
            raise AssetValidationError("uploaded object size mismatch")
        return self._update(asset.id, {"status": "ready"})

    def get_asset(self, *, owner_id: str, asset_id: str) -> Asset:
        response = (
            self.client.table("assets")
            .select("*")
            .eq("id", asset_id)
            .eq("owner_id", owner_id)
            .neq("status", "deleted")
            .limit(1)
            .execute()
        )
        return _asset(self._one(response.data))

    def list_assets(self, *, owner_id: str, thread_id: str) -> list[Asset]:
        response = (
            self.client.table("assets")
            .select("*")
            .eq("owner_id", owner_id)
            .eq("thread_id", thread_id)
            .neq("status", "deleted")
            .order("updated_at", desc=True)
            .execute()
        )
        rows = response.data if isinstance(response.data, list) else []
        return [_asset(row) for row in rows if isinstance(row, dict)]

    def download_ticket(
        self,
        *,
        owner_id: str,
        asset_id: str,
        expires_in: int = 300,
    ) -> AssetDownloadTicket:
        asset = self.get_asset(owner_id=owner_id, asset_id=asset_id)
        if asset.status != "ready":
            raise AssetValidationError("asset is not ready")
        response = self.bucket.create_signed_url(asset.object_path, expires_in)
        url = response.get("signedURL") or response.get("signedUrl")
        if not url:
            raise RuntimeError("Supabase returned no signed download URL")
        return AssetDownloadTicket(url=str(url), expires_in=expires_in)

    def download_bytes(self, *, owner_id: str, asset_id: str) -> tuple[Asset, bytes]:
        asset = self.get_asset(owner_id=owner_id, asset_id=asset_id)
        if asset.status != "ready":
            raise AssetValidationError("asset is not ready")
        content = self.bucket.download(asset.object_path)
        return asset, content

    def delete_asset(self, *, owner_id: str, asset_id: str) -> None:
        asset = self.get_asset(owner_id=owner_id, asset_id=asset_id)
        self.bucket.remove([asset.object_path])
        self._update(asset.id, {"status": "deleted"})

    def _upload_immutable(self, path: str, content: bytes, media_type: str) -> None:
        try:
            self.bucket.upload(
                path,
                content,
                file_options={
                    "content-type": media_type,
                    "cache-control": "3600",
                    "upsert": "false",
                },
            )
        except Exception:
            # A retry after a database/network failure can find the immutable
            # object already present. Treat only a confirmed object as success.
            self.bucket.info(path)

    def publish_artifact(
        self,
        *,
        owner_id: str,
        thread_id: str,
        turn_id: str,
        sandbox_path: str,
        content: bytes,
        media_type: str | None = None,
    ) -> Asset:
        if not sandbox_path.startswith("/workspace/artifacts/"):
            raise AssetValidationError("only /workspace/artifacts files are publishable")
        filename = safe_filename(sandbox_path)
        checksum = hashlib.sha256(content).hexdigest()
        logical_key = f"artifact:{sandbox_path}"
        existing = self._by_logical_key(thread_id, logical_key)
        if (
            existing is not None
            and existing.owner_id == owner_id
            and existing.status == "ready"
            and existing.sha256 == checksum
        ):
            return existing
        if existing is not None and existing.owner_id != owner_id:
            raise AssetValidationError("logical asset owner mismatch")

        asset_id = existing.id if existing else str(uuid4())
        object_path = f"{owner_id}/{thread_id}/{asset_id}/{checksum}/{filename}"
        content_type = media_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        old_object_path = existing.object_path if existing and existing.status == "ready" else None

        if existing is None:
            now = utc_now().isoformat()
            asset = self._insert(
                {
                    "id": asset_id,
                    "owner_id": owner_id,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "role": "artifact",
                    "status": "uploading",
                    "logical_key": logical_key,
                    "bucket_id": self.bucket_id,
                    "object_path": object_path,
                    "sandbox_path": sandbox_path,
                    "filename": filename,
                    "media_type": content_type,
                    "size_bytes": len(content),
                    "sha256": checksum,
                    "retention_class": "standard",
                    "created_at": now,
                    "updated_at": now,
                }
            )
        else:
            asset = existing

        try:
            self._upload_immutable(object_path, content, content_type)
            asset = self._update(
                asset_id,
                {
                    "turn_id": turn_id,
                    "status": "ready",
                    "object_path": object_path,
                    "sandbox_path": sandbox_path,
                    "filename": filename,
                    "media_type": content_type,
                    "size_bytes": len(content),
                    "sha256": checksum,
                },
            )
        except Exception:
            if existing is None:
                self._update(asset_id, {"status": "failed"})
            raise

        if old_object_path and old_object_path != object_path:
            try:
                self.bucket.remove([old_object_path])
            except Exception:
                pass
        return asset

    def require_ready_inputs(
        self,
        *,
        owner_id: str,
        thread_id: str,
        asset_ids: Sequence[str],
    ) -> list[Asset]:
        assets = [
            self.get_asset(owner_id=owner_id, asset_id=asset_id)
            for asset_id in dict.fromkeys(asset_ids)
        ]
        if any(
            asset.thread_id != thread_id or asset.role != "input" or asset.status != "ready"
            for asset in assets
        ):
            raise AssetValidationError("input assets must be ready and belong to the thread")
        return assets
