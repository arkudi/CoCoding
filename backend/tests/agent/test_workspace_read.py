import os

import pytest

from app.agent.workspace import WorkspaceError, WorkspaceService


def test_resolve_rejects_absolute_and_parent_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = WorkspaceService(workspace)
    with pytest.raises(WorkspaceError, match="PATH_OUTSIDE_WORKSPACE"):
        service.resolve(str(tmp_path / "outside.txt"))
    with pytest.raises(WorkspaceError, match="PATH_OUTSIDE_WORKSPACE"):
        service.resolve("../outside.txt")


def test_resolve_rejects_blank_path(tmp_path):
    with pytest.raises(WorkspaceError, match="PATH_OUTSIDE_WORKSPACE"):
        WorkspaceService(tmp_path).resolve("  ")


def test_read_file_uses_inclusive_one_based_lines(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = WorkspaceService(tmp_path).read_file("a.txt", start_line=2, end_line=3)
    assert result == {"path": "a.txt", "content": "two\nthree", "start_line": 2, "end_line": 3}


def test_rejects_symlink_escaping_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(WorkspaceError, match="PATH_OUTSIDE_WORKSPACE"):
        WorkspaceService(tmp_path).resolve("link.txt")


@pytest.mark.parametrize("path", ["missing.txt", "folder"])
def test_read_file_requires_existing_regular_file(tmp_path, path):
    (tmp_path / "folder").mkdir()
    with pytest.raises(WorkspaceError):
        WorkspaceService(tmp_path).read_file(path)


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, 99), (-1, None)])
def test_read_file_rejects_invalid_line_ranges(tmp_path, start, end):
    (tmp_path / "a.txt").write_text("one\ntwo", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="INVALID_LINE_RANGE"):
        WorkspaceService(tmp_path).read_file("a.txt", start_line=start, end_line=end)


def test_read_file_rejects_invalid_utf8(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe")
    with pytest.raises(WorkspaceError, match="INVALID_UTF8"):
        WorkspaceService(tmp_path).read_file("bad.txt")


def test_read_file_rejects_files_above_byte_limit(tmp_path):
    (tmp_path / "large.txt").write_bytes(b"x" * (1_048_576 + 1))
    with pytest.raises(WorkspaceError, match="FILE_TOO_LARGE"):
        WorkspaceService(tmp_path).read_file("large.txt")


def test_list_files_ignores_directories_and_sorts_posix_paths(tmp_path):
    (tmp_path / "z.txt").write_text("z")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.txt").write_text("b")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.txt").write_text("hidden")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("ignored")
    assert WorkspaceService(tmp_path).list_files() == {
        "files": ["a/b.txt", "z.txt"],
        "truncated": False,
    }


def test_list_files_reports_truncation_after_500_entries(tmp_path):
    for index in range(501):
        (tmp_path / f"{index:03d}.txt").write_text(str(index), encoding="utf-8")
    result = WorkspaceService(tmp_path).list_files()
    assert len(result["files"]) == 500
    assert result["files"][0] == "000.txt"
    assert result["files"][-1] == "499.txt"
    assert result["truncated"] is True


def test_list_files_can_list_subdirectory(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("a")
    assert WorkspaceService(tmp_path).list_files("sub") == {"files": ["sub/a.txt"], "truncated": False}


@pytest.mark.parametrize("ignored", sorted({".git", ".venv", "node_modules", "dist", "build", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", "htmlcov"}))
def test_list_files_rejects_ignored_directory_as_root(tmp_path, ignored):
    ignored_root = tmp_path / ignored
    ignored_root.mkdir()
    (ignored_root / "secret.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="PATH_IGNORED"):
        WorkspaceService(tmp_path).list_files(ignored)


def test_read_file_allows_empty_file_with_default_range(tmp_path):
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    assert WorkspaceService(tmp_path).read_file("empty.txt") == {
        "path": "empty.txt",
        "content": "",
        "start_line": 1,
        "end_line": 0,
    }
