from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.api.runs import router as runs_router
from app.api.workspace import router as workspace_router
from app.agent.provider import DeepSeekClient
from app.agent.events import RunEventHub
from app.agent.run_manager import RunManager
from app.agent.types import ModelClient
from app.agent.verifier import VerificationPolicy
from app.config import Settings, get_settings
from app.db.database import build_engine, build_session_factory, create_schema
from app.db.run_repository import RunRepository
from app.directory_picker import NativeDirectoryPicker


def create_app(
    settings: Settings | None = None,
    model_client: ModelClient | None = None,
    directory_picker=None,
) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(resolved.database_url)
        create_schema(engine)
        application.state.engine = engine
        application.state.session_factory = build_session_factory(engine)
        application.state.event_hub = RunEventHub()
        application.state.run_manager = RunManager(
            application.state.session_factory,
            application.state.event_hub,
            hard_step_limit=resolved.agent_hard_step_limit,
            verification_policy=VerificationPolicy(
                require_code_verification=resolved.agent_require_code_verification,
                allow_unverified_code_with_reason=(
                    resolved.agent_allow_unverified_code_with_reason
                ),
                require_resolved_test_failures=(
                    resolved.agent_require_resolved_test_failures
                ),
            ),
            multi_agent_enabled=resolved.agent_multi_agent_enabled,
            max_delegations=resolved.agent_max_delegations,
            child_step_limit=resolved.agent_child_step_limit,
            token_budget=resolved.agent_token_budget,
            tool_call_limit=resolved.agent_tool_call_limit,
            wall_clock_limit_seconds=resolved.agent_wall_clock_limit_seconds,
        )
        db = application.state.session_factory()
        try:
            RunRepository(db).interrupt_running_runs()
        finally:
            db.close()
        try:
            yield
        finally:
            application.state.run_manager.shutdown(wait=True)
            engine.dispose()

    app = FastAPI(title=resolved.app_name, lifespan=lifespan)
    app.state.settings = resolved
    app.state.model_client = model_client
    app.state.directory_picker = directory_picker or NativeDirectoryPicker().select
    app.state.production_model_client_factory = lambda: DeepSeekClient.from_settings(resolved)
    app.include_router(health_router, prefix="/api")
    app.include_router(sessions_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(workspace_router, prefix="/api")

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
