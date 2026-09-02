"""Safe, bounded access to files in a session workspace."""

import os
import difflib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


IGNORED_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    "htmlcov",
}
_NORMALIZED_IGNORED_PARTS = {os.path.normcase(part) for part in IGNORED_PARTS}
_TRACKING_IGNORED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log"}


def _is_ignored_component(part: str) -> bool:
    return os.path.normcase(part) in _NORMALIZED_IGNORED_PARTS


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class FileChangeEvidence:
    path: str
    operation: Literal["created", "modified", "deleted", "renamed"]
    before_hash: str | None
    after_hash: str
    unified_diff: str


@dataclass(frozen=True, slots=True)
class WorkspacePatch:
    path: str
    old_text: str | None
    new_text: str


@dataclass(frozen=True, slots=True)
class _TrackedFile:
    content_hash: str
    text: str | None


_DELETED_HASH = hashlib.sha256(b"<deleted>").hexdigest()


class WorkspaceService:
    def __init__(
        self,
        root: Path,
        max_text_bytes: int = 1_048_576,
        max_entries: int = 500,
        max_tracked_entries: int = 20_000,
    ):
        self.root = Path(root).resolve()
        self.max_text_bytes = max_text_bytes
        self.max_entries = max_entries
        self.max_tracked_entries = max_tracked_entries
        self._baseline: dict[str, _TrackedFile] | None = None

    def capture_baseline(self) -> None:
        """Capture the workspace once so every later mutation can be detected."""
        if self._baseline is None:
            self._baseline = self._scan_workspace()

    def resolve(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise WorkspaceError("PATH_OUTSIDE_WORKSPACE", "path must be non-blank and relative")
        candidate_input = Path(relative_path)
        if candidate_input.is_absolute():
            raise WorkspaceError("PATH_OUTSIDE_WORKSPACE", "absolute paths are not allowed")
        candidate = (self.root / candidate_input).resolve()
        if not candidate.is_relative_to(self.root):
            raise WorkspaceError("PATH_OUTSIDE_WORKSPACE", "path escapes workspace")
        relative_parts = candidate.relative_to(self.root).parts
        if any(_is_ignored_component(part) for part in relative_parts):
            raise WorkspaceError("PATH_IGNORED", "access to an ignored path is not allowed")
        return candidate

    def list_files(self, path: str = ".") -> dict[str, object]:
        base = self.resolve(path)
        if not base.exists():
            raise WorkspaceError("PATH_NOT_FOUND", "directory does not exist")
        if not base.is_dir():
            raise WorkspaceError("PATH_NOT_DIRECTORY", "listing path is not a directory")

        paths: list[str] = []
        seen: set[str] = set()
        reached_limit = False
        for current, directories, filenames in os.walk(base, topdown=True, followlinks=False):
            directories[:] = sorted(
                name for name in directories if not _is_ignored_component(name)
            )
            for name in sorted(filenames):
                file_path = Path(current) / name
                try:
                    resolved_file = file_path.resolve()
                except OSError:
                    continue
                if not resolved_file.is_relative_to(self.root) or not resolved_file.is_file():
                    continue
                resolved_parts = resolved_file.relative_to(self.root).parts
                if any(_is_ignored_component(part) for part in resolved_parts):
                    continue
                relative = file_path.relative_to(self.root).as_posix()
                if relative in seen:
                    continue
                seen.add(relative)
                paths.append(relative)
                if len(paths) > self.max_entries:
                    reached_limit = True
                    break
            if reached_limit:
                break
        paths.sort()
        return {"files": paths[: self.max_entries], "truncated": len(paths) > self.max_entries}

    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, object]:
        file_path = self.resolve(path)
        if not file_path.exists():
            raise WorkspaceError("PATH_NOT_FOUND", "file does not exist")
        if not file_path.is_file():
            raise WorkspaceError("PATH_NOT_FILE", "path is not a regular file")
        _, content = self._read_bounded_text(file_path)

        lines = content.splitlines()
        if start_line is not None and (isinstance(start_line, bool) or not isinstance(start_line, int)):
            raise WorkspaceError("INVALID_LINE_RANGE", "line numbers must be integers")
        if end_line is not None and (isinstance(end_line, bool) or not isinstance(end_line, int)):
            raise WorkspaceError("INVALID_LINE_RANGE", "line numbers must be integers")
        start = 1 if start_line is None else start_line
        end = len(lines) if end_line is None else end_line
        if not lines and start_line is None and end_line is None:
            return {"path": Path(path).as_posix(), "content": "", "start_line": 1, "end_line": 0}
        if start < 1 or end < start or end > len(lines):
            raise WorkspaceError("INVALID_LINE_RANGE", "line range is outside file")
        selected = "\n".join(lines[start - 1 : end])
        return {"path": Path(path).as_posix(), "content": selected, "start_line": start, "end_line": end}

    def write_file(self, path: str, content: str) -> dict[str, object]:
        self.capture_baseline()
        file_path = self.resolve(path)
        if not isinstance(content, str):
            raise WorkspaceError("INVALID_UTF8", "content must be text")
        encoded = content.encode("utf-8", errors="strict")
        if len(encoded) > self.max_text_bytes:
            raise WorkspaceError("FILE_TOO_LARGE", "content exceeds text byte limit")

        key = file_path.relative_to(self.root).as_posix()
        if file_path.exists() and not file_path.is_file():
            raise WorkspaceError("PATH_NOT_FILE", "path is not a regular file")
        if file_path.exists():
            try:
                self._read_bounded_text(file_path)
            except WorkspaceError as exc:
                if exc.code == "READ_FAILED":
                    raise WorkspaceError("WRITE_FAILED", exc.message) from exc
                raise

        parent = file_path.parent.resolve()
        if not parent.is_relative_to(self.root):
            raise WorkspaceError("PATH_OUTSIDE_WORKSPACE", "path escapes workspace")
        try:
            parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(encoded)
        except OSError as exc:
            raise WorkspaceError("WRITE_FAILED", "could not write file") from exc
        return self.read_file(key)

    def replace_in_file(self, path: str, old_text: str, new_text: str) -> dict[str, object]:
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise WorkspaceError("INVALID_UTF8", "replacement text must be strings")
        file_path = self.resolve(path)
        if not file_path.exists():
            raise WorkspaceError("PATH_NOT_FOUND", "file does not exist")
        if not file_path.is_file():
            raise WorkspaceError("PATH_NOT_FILE", "path is not a regular file")
        _, content = self._read_bounded_text(file_path)
        matches = content.count(old_text)
        if matches == 0:
            raise WorkspaceError("REPLACE_NO_MATCH", "old text was not found")
        if matches != 1:
            raise WorkspaceError("REPLACE_MULTIPLE_MATCHES", "old text occurs multiple times")
        return self.write_file(path, content.replace(old_text, new_text, 1))

    def apply_patch(self, patches: tuple[WorkspacePatch, ...]) -> dict[str, object]:
        """Validate every exact edit before applying any of them."""
        if not patches:
            raise WorkspaceError("INVALID_PATCH", "at least one patch is required")
        if len({patch.path for patch in patches}) != len(patches):
            raise WorkspaceError("INVALID_PATCH", "each path may appear only once")

        prepared: list[tuple[str, str]] = []
        for patch in patches:
            file_path = self.resolve(patch.path)
            if patch.old_text is None:
                if file_path.exists():
                    raise WorkspaceError("PATCH_TARGET_EXISTS", "new-file patch target already exists")
                updated = patch.new_text
            else:
                if not file_path.exists():
                    raise WorkspaceError("PATH_NOT_FOUND", "patch target does not exist")
                if not file_path.is_file():
                    raise WorkspaceError("PATH_NOT_FILE", "patch target is not a regular file")
                _, content = self._read_bounded_text(file_path)
                matches = content.count(patch.old_text)
                if matches == 0:
                    raise WorkspaceError("PATCH_NO_MATCH", "patch old_text was not found")
                if matches != 1:
                    raise WorkspaceError("PATCH_MULTIPLE_MATCHES", "patch old_text is not unique")
                updated = content.replace(patch.old_text, patch.new_text, 1)
            if len(updated.encode("utf-8", errors="strict")) > self.max_text_bytes:
                raise WorkspaceError("FILE_TOO_LARGE", "patched content exceeds text byte limit")
            prepared.append((patch.path, updated))

        for path, updated in prepared:
            self.write_file(path, updated)
        return {"paths": [Path(path).as_posix() for path, _ in prepared]}

    def search_text(
        self,
        query: str,
        path: str = ".",
        *,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> dict[str, object]:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query if regex else re.escape(query), flags)
        except re.error as exc:
            raise WorkspaceError("INVALID_SEARCH_PATTERN", "search regex is invalid") from exc

        matches: list[dict[str, object]] = []
        listing = self.list_files(path)
        truncated = bool(listing["truncated"])
        for relative in listing["files"]:
            file_path = self.root / str(relative)
            try:
                _, content = self._read_bounded_text(file_path)
            except WorkspaceError as exc:
                if exc.code in {"INVALID_UTF8", "FILE_TOO_LARGE", "READ_FAILED"}:
                    continue
                raise
            for line_number, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line) is None:
                    continue
                matches.append({
                    "path": str(relative),
                    "line": line_number,
                    "text": line[:500],
                })
                if len(matches) >= max_results:
                    return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": truncated}

    def changes(self) -> tuple[FileChangeEvidence, ...]:
        self.capture_baseline()
        assert self._baseline is not None
        current = self._scan_workspace()
        result: list[FileChangeEvidence] = []
        baseline_paths = set(self._baseline)
        current_paths = set(current)
        deleted = baseline_paths - current_paths
        created = current_paths - baseline_paths

        deleted_by_hash: dict[str, list[str]] = {}
        created_by_hash: dict[str, list[str]] = {}
        for path in deleted:
            deleted_by_hash.setdefault(self._baseline[path].content_hash, []).append(path)
        for path in created:
            created_by_hash.setdefault(current[path].content_hash, []).append(path)
        renamed: list[tuple[str, str]] = []
        for content_hash in deleted_by_hash.keys() & created_by_hash.keys():
            old_paths = deleted_by_hash[content_hash]
            new_paths = created_by_hash[content_hash]
            if len(old_paths) == 1 and len(new_paths) == 1:
                old_path, new_path = old_paths[0], new_paths[0]
                renamed.append((old_path, new_path))
                deleted.remove(old_path)
                created.remove(new_path)

        for old_path, new_path in sorted(renamed, key=lambda item: item[1]):
            snapshot = current[new_path]
            result.append(FileChangeEvidence(
                path=new_path,
                operation="renamed",
                before_hash=snapshot.content_hash,
                after_hash=snapshot.content_hash,
                unified_diff=(
                    "similarity index 100%\n"
                    f"rename from {old_path}\n"
                    f"rename to {new_path}\n"
                ),
            ))
        for key in sorted(created):
            after = current[key]
            result.append(FileChangeEvidence(
                path=key,
                operation="created",
                before_hash=None,
                after_hash=after.content_hash,
                unified_diff=self._change_diff(key, None, after.text, "created"),
            ))
        for key in sorted(deleted):
            before = self._baseline[key]
            result.append(FileChangeEvidence(
                path=key,
                operation="deleted",
                before_hash=before.content_hash,
                after_hash=_DELETED_HASH,
                unified_diff=self._change_diff(key, before.text, None, "deleted"),
            ))
        for key in sorted(baseline_paths & current_paths):
            before = self._baseline[key]
            after = current[key]
            if before.content_hash == after.content_hash:
                continue
            result.append(FileChangeEvidence(
                path=key,
                operation="modified",
                before_hash=before.content_hash,
                after_hash=after.content_hash,
                unified_diff=self._change_diff(key, before.text, after.text, "modified"),
            ))
        result.sort(key=lambda change: change.path)
        return tuple(result)

    def get_diff(self) -> str:
        return "".join(change.unified_diff for change in self.changes())

    def _read_bounded_text(self, file_path: Path) -> tuple[bytes, str]:
        try:
            with file_path.open("rb") as stream:
                raw = stream.read(self.max_text_bytes + 1)
        except OSError as exc:
            raise WorkspaceError("READ_FAILED", "could not read file") from exc
        if len(raw) > self.max_text_bytes:
            raise WorkspaceError("FILE_TOO_LARGE", "file exceeds text byte limit")
        try:
            return raw, raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("INVALID_UTF8", "file is not valid UTF-8") from exc

    def _scan_workspace(self) -> dict[str, _TrackedFile]:
        result: dict[str, _TrackedFile] = {}
        for current, directories, filenames in os.walk(
            self.root, topdown=True, followlinks=False
        ):
            directories[:] = sorted(
                name for name in directories if not _is_ignored_component(name)
            )
            for name in sorted(filenames):
                file_path = Path(current) / name
                if file_path.suffix.casefold() in _TRACKING_IGNORED_SUFFIXES:
                    continue
                try:
                    resolved = file_path.resolve()
                    if not resolved.is_relative_to(self.root) or not resolved.is_file():
                        continue
                    relative = file_path.relative_to(self.root).as_posix()
                    result[relative] = self._track_file(resolved)
                except OSError:
                    continue
                if len(result) > self.max_tracked_entries:
                    raise WorkspaceError(
                        "WORKSPACE_TRACKING_LIMIT",
                        "workspace contains too many files to track safely",
                    )
        return result

    def _track_file(self, file_path: Path) -> _TrackedFile:
        digest = hashlib.sha256()
        retained = bytearray()
        try:
            with file_path.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    digest.update(chunk)
                    if len(retained) <= self.max_text_bytes:
                        retained.extend(chunk[: self.max_text_bytes + 1 - len(retained)])
        except OSError as exc:
            raise WorkspaceError("READ_FAILED", "could not track workspace file") from exc
        text = None
        if len(retained) <= self.max_text_bytes:
            try:
                text = bytes(retained).decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                pass
        return _TrackedFile(digest.hexdigest(), text)

    @staticmethod
    def _change_diff(
        path: str,
        before: str | None,
        after: str | None,
        operation: Literal["created", "modified", "deleted"],
    ) -> str:
        if (
            (operation == "created" and after is None)
            or (operation == "deleted" and before is None)
            or (operation == "modified" and (before is None or after is None))
        ):
            return f"Binary file {path} changed\n"
        if before is None:
            before = ""
        if after is None:
            after = ""
        return WorkspaceService._unified_diff(path, before, after)

    @staticmethod
    def _unified_diff(path: str, before: str, after: str) -> str:
        return "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
