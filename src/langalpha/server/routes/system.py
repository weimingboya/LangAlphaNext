import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from langalpha.config import Settings
from langalpha.domain.models import PublicConfig
from langalpha.server.dependencies import ServicesDep, UserDep

router = APIRouter()


def readiness_issues(settings: Settings) -> list[str]:
    issues: list[str] = []
    required = (
        ("SUPABASE_URL", settings.require_supabase_url),
        ("SUPABASE_PUBLISHABLE_KEY", settings.require_supabase_publishable_key),
        ("SUPABASE_SECRET_KEY", settings.require_supabase_secret_key),
        ("DAYTONA_API_KEY", settings.require_daytona_key),
    )
    for name, resolve in required:
        try:
            resolve()
        except RuntimeError:
            issues.append(name)

    parsed = urlparse(settings.langgraph_server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        issues.append("LANGGRAPH_SERVER_URL")
    elif settings.app_environment == "production" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        issues.append("LANGGRAPH_SERVER_URL")
    if settings.app_environment == "production" and settings.langgraph_api_key is None:
        issues.append("LANGGRAPH_API_KEY")
    if not settings.langgraph_assistant_id:
        issues.append("LANGGRAPH_ASSISTANT_ID")
    if settings.app_environment == "production" and settings.app_version == "development":
        issues.append("APP_VERSION")
    return sorted(set(issues))


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(services: ServicesDep) -> dict[str, str]:
    issues = readiness_issues(services.settings)
    if issues:
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "missing_or_invalid": issues},
        )
    checks = await asyncio.gather(
        services.gateway.healthcheck(services.settings.langgraph_assistant_id),
        asyncio.to_thread(services.project_store.healthcheck),
        return_exceptions=True,
    )
    unavailable = [
        name
        for name, result in zip(("LANGGRAPH", "SUPABASE"), checks, strict=True)
        if isinstance(result, BaseException)
    ]
    if unavailable:
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "unavailable": unavailable},
        )
    return {"status": "ready"}


@router.get("/api/config", response_model=PublicConfig)
async def public_config(services: ServicesDep) -> PublicConfig:
    settings = services.settings
    try:
        return PublicConfig(
            supabase_url=settings.require_supabase_url(),
            supabase_publishable_key=settings.require_supabase_publishable_key(),
            storage_bucket=settings.supabase_storage_bucket,
            max_upload_bytes=settings.max_upload_bytes,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/auth/me")
async def auth_me(user: UserDep) -> dict[str, str | None]:
    return {"id": user.id, "email": user.email}
