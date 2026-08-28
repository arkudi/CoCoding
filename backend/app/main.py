from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.config import Settings, get_settings
from app.db.database import build_engine, build_session_factory, create_schema


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    app = FastAPI(title=resolved.app_name)
    engine = build_engine(resolved.database_url)
    create_schema(engine)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.include_router(health_router, prefix="/api")
    app.include_router(sessions_router, prefix="/api")

    assets = resolved.frontend_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_root():
        index = resolved.frontend_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return {
            "app": resolved.app_name,
            "message": "Frontend build not found; run the Vite development server.",
        }

    return app


app = create_app()
