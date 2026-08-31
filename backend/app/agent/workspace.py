"""Safe, bounded access to files in a session workspace."""

import os
from pathlib import Path


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


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


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

    def resolve(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise WorkspaceError("PATH_OUTSIDE_WORKSPACE", "path must be non-blank and relative")
        candidate_input = Path(relative_path)
        if candidate_input.is_absolute():
            raise WorkspaceError("PATH_OUTSIDE_WORKSPACE", "absolute paths are not allowed")
        candidate = (self.root / candidate_input).resolve()
        if not candidate.is_relative_to(self.root):
            raise WorkspaceError("PATH_OUTSIDE_WORKSPACE", "path escapes workspace")
        return candidate

    def list_files(self, path: str = ".") -> dict[str, object]:
        base = self.resolve(path)
        if not base.exists():
            raise WorkspaceError("PATH_NOT_FOUND", "directory does not exist")
        if not base.is_dir():
            raise WorkspaceError("PATH_NOT_DIRECTORY", "listing path is not a directory")

        paths: list[str] = []
        for current, directories, filenames in os.walk(base, topdown=True, followlinks=False):
            directories[:] = sorted(name for name in directories if name not in IGNORED_PARTS)
            for name in sorted(filenames):
                file_path = Path(current) / name
                try:
                    resolved_file = file_path.resolve()
                except OSError:
                    continue
                if not resolved_file.is_relative_to(self.root) or not resolved_file.is_file():
                    continue
                relative = file_path.relative_to(self.root).as_posix()
                if relative not in paths:
                    paths.append(relative)
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
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            raise WorkspaceError("READ_FAILED", "could not stat file") from exc
        if size > self.max_text_bytes:
            raise WorkspaceError("FILE_TOO_LARGE", "file exceeds text byte limit")
        try:
            content = file_path.read_bytes().decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("INVALID_UTF8", "file is not valid UTF-8") from exc
        except OSError as exc:
            raise WorkspaceError("READ_FAILED", "could not read file") from exc

        lines = content.splitlines()
        if start_line is not None and (isinstance(start_line, bool) or not isinstance(start_line, int)):
            raise WorkspaceError("INVALID_LINE_RANGE", "line numbers must be integers")
        if end_line is not None and (isinstance(end_line, bool) or not isinstance(end_line, int)):
            raise WorkspaceError("INVALID_LINE_RANGE", "line numbers must be integers")
        start = 1 if start_line is None else start_line
        end = len(lines) if end_line is None else end_line
        if start < 1 or end < start or end > len(lines):
            raise WorkspaceError("INVALID_LINE_RANGE", "line range is outside file")
        selected = "\n".join(lines[start - 1 : end])
        return {"path": Path(path).as_posix(), "content": selected, "start_line": start, "end_line": end}
