from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    app = FastAPI(title=resolved.app_name)
    app.state.settings = resolved
    app.include_router(health_router, prefix="/api")
    return app


app = create_app()
