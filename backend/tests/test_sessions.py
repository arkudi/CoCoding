from pathlib import Path

from fastapi.testclient import TestClient


def test_create_and_list_session(client: TestClient, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = client.post(
        "/api/sessions",
        json={"title": "Fix calculator", "workspace_path": str(workspace)},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["title"] == "Fix calculator"
    assert payload["workspace_path"] == str(workspace.resolve())
    assert payload["status"] == "idle"
    assert payload["id"]

    listed = client.get("/api/sessions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [payload["id"]]


def test_create_session_rejects_missing_workspace(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.post(
        "/api/sessions",
        json={"title": "Invalid", "workspace_path": str(tmp_path / "missing")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Workspace directory does not exist"
