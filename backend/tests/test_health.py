from fastapi.testclient import TestClient


def test_health_returns_application_identity(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "CoCoding"}
