import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.config import Settings
from app.db.models import RunRecord, SessionRecord
from app.main import create_app


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_importing_asgi_app_does_not_initialize_database(tmp_path: Path) -> None:
    database_path = tmp_path / "import.db"
    environment = os.environ.copy()
    environment["COCODING_DATABASE_URL"] = sqlite_url(database_path)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

    imported = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print(app.title)"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "CoCoding"
    assert not database_path.exists()


def test_creating_app_defers_database_initialization(tmp_path: Path) -> None:
    database_path = tmp_path / "create.db"

    create_app(
        Settings(
            database_url=sqlite_url(database_path),
            frontend_dist=tmp_path / "missing-dist",
        )
    )

    assert not database_path.exists()


def test_testclient_runs_database_startup_and_shutdown(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecycle.db"
    application = create_app(
        Settings(
            database_url=sqlite_url(database_path),
            frontend_dist=tmp_path / "missing-dist",
        )
    )

    assert not database_path.exists()
    with TestClient(application) as client:
        assert database_path.is_file()
        assert client.get("/api/health").status_code == 200
        dispose = Mock(wraps=application.state.engine.dispose)
        application.state.engine.dispose = dispose

    dispose.assert_called_once_with()


def test_startup_interrupts_running_runs(tmp_path: Path) -> None:
    database_path = tmp_path / "recovery.db"
    application = create_app(
        Settings(
            database_url=sqlite_url(database_path),
            frontend_dist=tmp_path / "missing-dist",
        )
    )

    with TestClient(application):
        db = application.state.session_factory()
        session = SessionRecord(title="Workspace", workspace_path="C:/workspace")
        db.add(session)
        db.flush()
        run = RunRecord(
            session_id=session.id,
            prompt="continue",
            model="fake",
            prompt_version="v1",
            max_steps=1,
        )
        db.add(run)
        db.commit()
        run_id = run.id
        db.close()

    with TestClient(application):
        db = application.state.session_factory()
        recovered = db.get(RunRecord, run_id)
        assert recovered is not None
        assert recovered.status == "interrupted"
        assert recovered.error_text == "Run interrupted during startup recovery."
        db.close()
