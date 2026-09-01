"""Safe, bounded access to files in a session workspace."""

import os
import difflib
import hashlib
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
    operation: Literal["created", "modified"]
    before_hash: str | None
    after_hash: str
    unified_diff: str


class WorkspaceService:
    def __init__(
        self,
        root: Path,
        max_text_bytes: int = 1_048_576,
        max_entries: int = 500,
    ):
        self.root = Path(root).resolve()
        self.max_text_bytes = max_text_bytes
        self.max_entries = max_entries
        self._snapshots: dict[str, str | None] = {}

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
        file_path = self.resolve(path)
        if not isinstance(content, str):
            raise WorkspaceError("INVALID_UTF8", "content must be text")
        encoded = content.encode("utf-8", errors="strict")
        if len(encoded) > self.max_text_bytes:
            raise WorkspaceError("FILE_TOO_LARGE", "content exceeds text byte limit")

        key = file_path.relative_to(self.root).as_posix()
        if file_path.exists() and not file_path.is_file():
            raise WorkspaceError("PATH_NOT_FILE", "path is not a regular file")
        before: str | None = None
        if file_path.exists():
            try:
                _, before = self._read_bounded_text(file_path)
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
        if key not in self._snapshots:
            self._snapshots[key] = before
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

    def changes(self) -> tuple[FileChangeEvidence, ...]:
        result: list[FileChangeEvidence] = []
        for key, before in sorted(self._snapshots.items()):
            file_path = self.root / key
            try:
                after_bytes, after = self._read_bounded_text(file_path)
            except WorkspaceError as exc:
                if exc.code == "READ_FAILED":
                    raise WorkspaceError("READ_FAILED", "could not read changed file") from exc
                raise
            before_bytes = None if before is None else before.encode("utf-8")
            result.append(FileChangeEvidence(
                path=key,
                operation="created" if before is None else "modified",
                before_hash=None if before_bytes is None else hashlib.sha256(before_bytes).hexdigest(),
                after_hash=hashlib.sha256(after_bytes).hexdigest(),
                unified_diff=self._unified_diff(key, before or "", after),
            ))
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

    @staticmethod
    def _unified_diff(path: str, before: str, after: str) -> str:
        return "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
