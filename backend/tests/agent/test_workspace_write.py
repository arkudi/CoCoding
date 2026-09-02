import hashlib

import pytest

from app.agent.workspace import (
    FileChangeEvidence,
    WorkspaceError,
    WorkspacePatch,
    WorkspaceService,
)


def test_write_snapshots_original_only_once_and_builds_diff(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"before\n")
    service = WorkspaceService(tmp_path)
    service.write_file("a.txt", "middle\n")
    service.write_file("a.txt", "after\n")

    changes = service.changes()
    assert len(changes) == 1
    assert changes[0].operation == "modified"
    assert "-before" in changes[0].unified_diff
    assert "+after" in changes[0].unified_diff
    assert changes[0].before_hash == hashlib.sha256(b"before\n").hexdigest()
    assert changes[0].after_hash == hashlib.sha256((tmp_path / "a.txt").read_bytes()).hexdigest()


def test_write_new_nested_file_is_created(tmp_path):
    service = WorkspaceService(tmp_path)
    service.write_file("nested/new.txt", "hello\n")
    change = service.changes()[0]
    assert change == FileChangeEvidence(
        path="nested/new.txt", operation="created", before_hash=None,
        after_hash=hashlib.sha256(b"hello\n").hexdigest(),
        unified_diff="--- a/nested/new.txt\n+++ b/nested/new.txt\n@@ -0,0 +1 @@\n+hello\n",
    )


def test_write_rejects_oversized_and_non_utf8_content(tmp_path):
    service = WorkspaceService(tmp_path)
    with pytest.raises(WorkspaceError, match="FILE_TOO_LARGE"):
        service.write_file("large.txt", "x" * 1_048_577)
    with pytest.raises(WorkspaceError, match="INVALID_UTF8"):
        service.write_file("bad.txt", b"bytes")
    assert not (tmp_path / "large.txt").exists()


def test_write_rejects_oversized_existing_file(tmp_path):
    (tmp_path / "large.txt").write_bytes(b"x" * 1_048_577)
    with pytest.raises(WorkspaceError, match="FILE_TOO_LARGE"):
        WorkspaceService(tmp_path).write_file("large.txt", "replacement")


def test_replace_rejects_oversized_existing_file(tmp_path):
    (tmp_path / "large.txt").write_bytes(b"x" * 1_048_577)
    with pytest.raises(WorkspaceError, match="FILE_TOO_LARGE"):
        WorkspaceService(tmp_path).replace_in_file("large.txt", "x", "y")


def test_changes_and_get_diff_track_externally_oversized_file(tmp_path):
    service = WorkspaceService(tmp_path)
    service.write_file("a.txt", "small")
    (tmp_path / "a.txt").write_bytes(b"x" * 1_048_577)

    change = service.changes()[0]

    assert change.operation == "created"
    assert change.before_hash is None
    assert service.get_diff() == "Binary file a.txt changed\n"


def test_write_rejects_traversal_and_does_not_create_parent(tmp_path):
    service = WorkspaceService(tmp_path)
    with pytest.raises(WorkspaceError, match="PATH_OUTSIDE_WORKSPACE"):
        service.write_file("../outside/new.txt", "x")
    assert not (tmp_path.parent / "outside").exists()


def test_replace_requires_exactly_one_match(tmp_path):
    (tmp_path / "a.txt").write_text("one two one", encoding="utf-8")
    service = WorkspaceService(tmp_path)
    with pytest.raises(WorkspaceError, match="REPLACE_NO_MATCH"):
        service.replace_in_file("a.txt", "missing", "new")
    with pytest.raises(WorkspaceError, match="REPLACE_MULTIPLE_MATCHES"):
        service.replace_in_file("a.txt", "one", "new")
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "one two one"


def test_replace_writes_single_match(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"one two\n")
    service = WorkspaceService(tmp_path)
    result = service.replace_in_file("a.txt", "one", "three")
    assert result["path"] == "a.txt"
    assert result["content"] == "three two"
    assert (tmp_path / "a.txt").read_bytes() == b"three two\n"


def test_apply_patch_validates_all_edits_before_writing(tmp_path):
    (tmp_path / "a.txt").write_text("before a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("before b", encoding="utf-8")
    service = WorkspaceService(tmp_path)

    with pytest.raises(WorkspaceError, match="PATCH_NO_MATCH"):
        service.apply_patch((
            WorkspacePatch("a.txt", "before", "after"),
            WorkspacePatch("b.txt", "missing", "after"),
        ))

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "before a"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "before b"


def test_apply_patch_can_create_a_new_file(tmp_path):
    service = WorkspaceService(tmp_path)

    result = service.apply_patch((WorkspacePatch("new.txt", None, "created\n"),))

    assert result == {"paths": ["new.txt"]}
    assert service.changes()[0].operation == "created"


def test_get_diff_covers_all_changed_files_in_sorted_order(tmp_path):
    service = WorkspaceService(tmp_path)
    service.write_file("z.txt", "z")
    service.write_file("a.txt", "a")
    diff = service.get_diff()
    assert diff.index("a/a.txt") < diff.index("a/z.txt")
    assert "+a" in diff and "+z" in diff


def test_baseline_detects_external_create_modify_and_delete(tmp_path):
    (tmp_path / "modified.txt").write_text("before\n", encoding="utf-8")
    (tmp_path / "deleted.txt").write_text("remove\n", encoding="utf-8")
    service = WorkspaceService(tmp_path)
    service.capture_baseline()

    (tmp_path / "modified.txt").write_text("after\n", encoding="utf-8")
    (tmp_path / "deleted.txt").unlink()
    (tmp_path / "created.txt").write_text("new\n", encoding="utf-8")

    changes = {change.path: change for change in service.changes()}
    assert changes["modified.txt"].operation == "modified"
    assert changes["deleted.txt"].operation == "deleted"
    assert changes["created.txt"].operation == "created"
    assert "-remove" in changes["deleted.txt"].unified_diff


def test_baseline_recognizes_unambiguous_external_rename(tmp_path):
    (tmp_path / "old.txt").write_text("same content\n", encoding="utf-8")
    service = WorkspaceService(tmp_path)
    service.capture_baseline()

    (tmp_path / "old.txt").rename(tmp_path / "new.txt")

    assert service.changes()[0].operation == "renamed"
    assert service.changes()[0].path == "new.txt"
    assert "rename from old.txt" in service.changes()[0].unified_diff


def test_changes_are_immutable(tmp_path):
    service = WorkspaceService(tmp_path)
    service.write_file("a.txt", "a")
    changes = service.changes()
    with pytest.raises(AttributeError):
        changes[0].path = "other.txt"
    with pytest.raises(AttributeError):
        changes.append(changes[0])
