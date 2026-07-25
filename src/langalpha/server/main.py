from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from langalpha.assets.store import AssetStore
from langalpha.config import Settings, get_settings
from langalpha.server.agent_gateway import AgentGateway
from langalpha.server.auth import Authenticator
from langalpha.server.dependencies import AppServices
from langalpha.server.routes import assets, runs, system, threads

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def create_app(
    settings: Settings | None = None,
    *,
    gateway: AgentGateway | None = None,
    authenticator: Authenticator | None = None,
    asset_store: AssetStore | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    graph = gateway or AgentGateway(
        app_settings.langgraph_server_url,
        api_key=(
            app_settings.langgraph_api_key.get_secret_value()
            if app_settings.langgraph_api_key is not None
            else None
        ),
    )
    services = AppServices(
        app_settings,
        graph,
        authenticator=authenticator,
        asset_store=asset_store,
    )

    app = FastAPI(title="LangAlpha BFF", version="1.0.0")
    app.state.services = services
    app.state.settings = app_settings
    app.state.agent_gateway = graph

    web_dir = _PROJECT_ROOT / "public"
    app.mount("/static", StaticFiles(directory=web_dir / "static", check_dir=False), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        if not (web_dir / "index.html").is_file():
            raise HTTPException(
                status_code=503,
                detail="Web build is unavailable. Run `npm --prefix web run build`.",
            )
        return FileResponse(web_dir / "index.html")

    app.include_router(system.router)
    app.include_router(threads.router)
    app.include_router(runs.router)
    app.include_router(assets.router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "langalpha.server.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
