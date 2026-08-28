from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_serves_built_frontend(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>CoCoding UI</h1>", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        frontend_dist=dist,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "CoCoding UI" in response.text


def test_missing_frontend_returns_api_hint(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "app": "CoCoding",
        "message": "Frontend build not found; run the Vite development server.",
    }
