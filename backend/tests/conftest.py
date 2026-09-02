from collections.abc import Iterator
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        frontend_dist=tmp_path / "missing-dist",
        agent_multi_agent_enabled=False,
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def app_factory(tmp_path: Path):
    def factory(model_client):
        settings = Settings(
            database_url=f"sqlite:///{tmp_path / 'runs.db'}",
            frontend_dist=tmp_path / "missing-dist",
            deepseek_api_key="",
            agent_multi_agent_enabled=False,
        )
        return TestClient(create_app(settings, model_client=model_client))

    return factory
