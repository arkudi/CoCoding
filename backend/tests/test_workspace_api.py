from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_runs import _create_session


def test_workspace_api_lists_and_reads_relative_text(client: TestClient, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    session = _create_session(client, workspace)

    listing = client.get(f"/api/sessions/{session['id']}/files")
    content = client.get(
        f"/api/sessions/{session['id']}/files/content",
        params={"path": "src/main.py"},
    )

    assert listing.status_code == 200
    assert listing.json() == {"files": ["src/main.py"], "truncated": False}
    assert content.status_code == 200
    assert content.json()["content"] == "print('ok')"
    assert content.json()["path"] == "src/main.py"


@pytest.mark.parametrize("path", ["../outside.txt", "C:/outside.txt"])
def test_workspace_api_rejects_escape(
    client: TestClient, tmp_path: Path, path: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = _create_session(client, workspace)

    response = client.get(
        f"/api/sessions/{session['id']}/files/content", params={"path": path}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PATH_OUTSIDE_WORKSPACE"


def test_workspace_api_returns_safe_binary_and_unavailable_errors(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "binary.bin").write_bytes(b"\xff\xfe")
    session = _create_session(client, workspace)

    binary = client.get(
        f"/api/sessions/{session['id']}/files/content",
        params={"path": "binary.bin"},
    )
    (workspace / "binary.bin").unlink()
    workspace.rmdir()
    unavailable = client.get(f"/api/sessions/{session['id']}/files")

    assert binary.status_code == 415
    assert binary.json()["detail"]["code"] == "INVALID_UTF8"
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "WORKSPACE_UNAVAILABLE"
