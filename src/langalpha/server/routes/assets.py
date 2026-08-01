import asyncio
import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from langalpha.assets.store import safe_filename
from langalpha.domain.models import (
    Asset,
    AssetComplete,
    AssetDownloadTicket,
    AssetUploadCreate,
    AssetUploadTicket,
)
from langalpha.server.dependencies import ServicesDep, UserDep, asset_http_error

router = APIRouter(prefix="/api")
_HTML_ASSET_CSP = (
    "sandbox allow-scripts; "
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "img-src data: blob:; "
    "font-src data:; "
    "media-src data: blob:; "
    "connect-src 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)
_INLINE_APPLICATION_TYPES = {
    "application/json",
    "application/pdf",
    "application/xhtml+xml",
    "application/xml",
}
_UNSAFE_INLINE_TYPES = {"image/svg+xml"}


def _inline_media_type(filename: str, declared_media_type: str) -> str | None:
    declared = declared_media_type.partition(";")[0].strip().lower()
    guessed = (mimetypes.guess_type(filename)[0] or "").lower()
    media_type = guessed if declared in {"", "application/octet-stream"} else declared
    if media_type in _UNSAFE_INLINE_TYPES:
        return None
    if media_type.startswith(("text/", "image/", "audio/", "video/")):
        return media_type
    if media_type in _INLINE_APPLICATION_TYPES:
        return media_type
    return None


@router.post(
    "/projects/{project_id}/assets/uploads",
    response_model=AssetUploadTicket,
    status_code=201,
)
async def create_asset_upload(
    project_id: str,
    body: AssetUploadCreate,
    user: UserDep,
    services: ServicesDep,
) -> AssetUploadTicket:
    project = await services.require_project(project_id, user)
    if body.size_bytes > services.settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="file exceeds upload limit")
    try:
        return await asyncio.to_thread(
            services.asset_store.create_upload,
            owner_id=user.id,
            project_id=project.id,
            request=body,
        )
    except Exception as exc:
        raise asset_http_error(exc) from exc


@router.post("/assets/{asset_id}/complete", response_model=Asset)
async def complete_asset_upload(
    asset_id: str,
    body: AssetComplete,
    user: UserDep,
    services: ServicesDep,
) -> Asset:
    try:
        return await asyncio.to_thread(
            services.asset_store.complete_upload,
            owner_id=user.id,
            asset_id=asset_id,
            sha256=body.sha256,
        )
    except Exception as exc:
        raise asset_http_error(exc) from exc


@router.get("/projects/{project_id}/assets", response_model=list[Asset])
async def list_assets(
    project_id: str,
    user: UserDep,
    services: ServicesDep,
) -> list[Asset]:
    project = await services.require_project(project_id, user)
    try:
        return await asyncio.to_thread(
            services.asset_store.list_assets,
            owner_id=user.id,
            project_id=project.id,
        )
    except Exception as exc:
        raise asset_http_error(exc) from exc


@router.get("/assets/{asset_id}", response_model=Asset)
async def get_asset(
    asset_id: str,
    user: UserDep,
    services: ServicesDep,
) -> Asset:
    try:
        return await asyncio.to_thread(
            services.asset_store.get_asset,
            owner_id=user.id,
            asset_id=asset_id,
        )
    except Exception as exc:
        raise asset_http_error(exc) from exc


@router.post("/assets/{asset_id}/download-url", response_model=AssetDownloadTicket)
async def asset_download_url(
    asset_id: str,
    user: UserDep,
    services: ServicesDep,
) -> AssetDownloadTicket:
    try:
        return await asyncio.to_thread(
            services.asset_store.download_ticket,
            owner_id=user.id,
            asset_id=asset_id,
        )
    except Exception as exc:
        raise asset_http_error(exc) from exc


@router.get("/assets/{asset_id}/view")
async def view_asset(
    asset_id: str,
    user: UserDep,
    services: ServicesDep,
) -> Response:
    try:
        asset, content = await asyncio.to_thread(
            services.asset_store.download_bytes,
            owner_id=user.id,
            asset_id=asset_id,
        )
    except Exception as exc:
        raise asset_http_error(exc) from exc
    media_type = _inline_media_type(asset.filename, asset.media_type)
    if media_type is None:
        raise HTTPException(
            status_code=415,
            detail="this file format does not support inline preview",
        )
    headers = {
        "Content-Disposition": f'inline; filename="{safe_filename(asset.filename)}"',
        "X-Content-Type-Options": "nosniff",
    }
    if media_type in {"text/html", "application/xhtml+xml"}:
        headers["Content-Security-Policy"] = _HTML_ASSET_CSP
    return Response(
        content=content,
        media_type=media_type,
        headers=headers,
    )


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: str,
    user: UserDep,
    services: ServicesDep,
) -> Response:
    try:
        await asyncio.to_thread(
            services.asset_store.delete_asset,
            owner_id=user.id,
            asset_id=asset_id,
        )
    except Exception as exc:
        raise asset_http_error(exc) from exc
    return Response(status_code=204)
