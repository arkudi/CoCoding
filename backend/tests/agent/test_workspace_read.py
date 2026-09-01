import errno
import os
from pathlib import Path

import pytest

from app.agent.workspace import WorkspaceError, WorkspaceService


_WINDOWS_SYMLINK_UNAVAILABLE_ERRORS = {
    errno.EACCES,
    errno.ENOSYS,
    errno.EPERM,
}
_WINDOWS_SYMLINK_UNAVAILABLE_WINERRORS = {1, 5, 50, 1314}


def _create_symlink_or_skip(link, target, *, is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except NotImplementedError:
        if os.name == "nt":
            pytest.skip("symlinks unavailable on this Windows host")
        raise
    except OSError as error:
        unavailable = os.name == "nt" and (
            error.errno in _WINDOWS_SYMLINK_UNAVAILABLE_ERRORS
            or getattr(error, "winerror", None)
            in _WINDOWS_SYMLINK_UNAVAILABLE_WINERRORS
        )
        if unavailable:
            pytest.skip("symlink privilege or capability unavailable on Windows")
        raise


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
    _create_symlink_or_skip(link, target)
    with pytest.raises(WorkspaceError, match="PATH_OUTSIDE_WORKSPACE"):
        WorkspaceService(tmp_path).resolve("link.txt")


def test_symlink_capability_helper_reraises_unrelated_oserror(
    tmp_path, monkeypatch
):
    def fail_with_unrelated_error(*args, **kwargs):
        raise OSError(errno.EIO, "unrelated filesystem failure")

    monkeypatch.setattr(Path, "symlink_to", fail_with_unrelated_error)

    with pytest.raises(OSError, match="unrelated filesystem failure"):
        _create_symlink_or_skip(tmp_path / "link", tmp_path / "target")


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


def test_bounded_text_read_opens_once_and_requests_only_limit_plus_one(
    tmp_path, monkeypatch
):
    target = tmp_path / "growing.txt"
    target.write_bytes(b"x")
    opened = 0
    read_sizes = []

    class GrowingStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            read_sizes.append(size)
            return b"x" * 5

    def open_growing_file(self, *args, **kwargs):
        nonlocal opened
        assert self == target
        opened += 1
        return GrowingStream()

    monkeypatch.setattr(Path, "open", open_growing_file)

    with pytest.raises(WorkspaceError, match="FILE_TOO_LARGE"):
        WorkspaceService(tmp_path, max_text_bytes=4).read_file("growing.txt")

    assert opened == 1
    assert read_sizes == [5]


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


def test_list_files_stops_traversal_after_one_entry_beyond_limit(
    tmp_path, monkeypatch
):
    for index in range(10):
        directory = tmp_path / f"d{index}"
        directory.mkdir()
        (directory / "file.txt").write_text(str(index), encoding="utf-8")
    visited = 0

    def instrumented_walk(*args, **kwargs):
        nonlocal visited
        for index in range(10):
            visited += 1
            yield str(tmp_path / f"d{index}"), [], ["file.txt"]

    monkeypatch.setattr("app.agent.workspace.os.walk", instrumented_walk)

    result = WorkspaceService(tmp_path, max_entries=2).list_files()

    assert result == {
        "files": ["d0/file.txt", "d1/file.txt"],
        "truncated": True,
    }
    assert visited == 3


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


def test_resolved_symlink_alias_cannot_enter_ignored_directory(tmp_path):
    ignored = tmp_path / ".git"
    ignored.mkdir()
    (ignored / "secret.txt").write_text("secret", encoding="utf-8")
    alias = tmp_path / "metadata"
    _create_symlink_or_skip(alias, ignored, is_directory=True)

    with pytest.raises(WorkspaceError, match="PATH_IGNORED"):
        WorkspaceService(tmp_path).read_file("metadata/secret.txt")


def test_listing_omits_symlink_alias_to_file_in_ignored_directory(tmp_path):
    ignored = tmp_path / ".git"
    ignored.mkdir()
    secret = ignored / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    alias = tmp_path / "metadata.txt"
    _create_symlink_or_skip(alias, secret)

    assert WorkspaceService(tmp_path).list_files() == {
        "files": [],
        "truncated": False,
    }


def test_ignored_directory_case_variant_is_rejected_on_case_insensitive_filesystem(
    tmp_path,
):
    ignored = tmp_path / ".git"
    ignored.mkdir()
    (ignored / "secret.txt").write_text("secret", encoding="utf-8")
    variant = tmp_path / ".GIT"
    try:
        same_directory = variant.samefile(ignored)
    except OSError:
        same_directory = False
    if not same_directory:
        pytest.skip("filesystem is case-sensitive")

    with pytest.raises(WorkspaceError, match="PATH_IGNORED"):
        WorkspaceService(tmp_path).read_file(".GIT/secret.txt")


def test_listing_prunes_ignored_case_variant_on_case_insensitive_filesystem(
    tmp_path,
):
    variant = tmp_path / ".GIT"
    variant.mkdir()
    (variant / "secret.txt").write_text("secret", encoding="utf-8")
    canonical = tmp_path / ".git"
    try:
        same_directory = canonical.samefile(variant)
    except OSError:
        same_directory = False
    if not same_directory:
        pytest.skip("filesystem is case-sensitive")

    assert WorkspaceService(tmp_path).list_files() == {
        "files": [],
        "truncated": False,
    }


def test_read_file_allows_empty_file_with_default_range(tmp_path):
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    assert WorkspaceService(tmp_path).read_file("empty.txt") == {
        "path": "empty.txt",
        "content": "",
        "start_line": 1,
        "end_line": 0,
    }
