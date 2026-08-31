# Task 2 report: workspace containment, listing, and reads

## Status

Implemented and committed as `feat: add safe workspace reads`.

## RED evidence

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_workspace_read.py -v
```

The test collection failed as expected before implementation because `app.agent.workspace` did not exist (`ModuleNotFoundError: No module named 'app.agent.workspace'`).

## GREEN evidence

Focused command:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/agent/test_workspace_read.py -q
```

Output: `14 passed, 1 skipped, 1 warning in 0.77s`.

Full backend suite command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Output: `33 passed, 1 skipped, 1 warning in 3.21s`.

The one skipped test is the escaping-symlink test because this Windows environment cannot create symlinks. The warning is the existing Starlette/httpx deprecation warning.

## Changes

- Added `WorkspaceError` with stable error code and message fields.
- Added `WorkspaceService` with resolved-root containment checks rejecting blank, absolute, parent-traversal, and escaping symlink paths.
- Added deterministic recursive listings with the required ignored directory names, POSIX paths, 500-entry cap, and truncation flag.
- Added bounded strict UTF-8 reads and inclusive one-based line slicing.
- Added tests for containment, symlinks, missing/non-file paths, line ranges, encoding, size limits, ignored directories, sorting, truncation, and subdirectory listing.

## Self-review

The implementation is limited to the requested workspace service and tests. Reads check file size before loading bytes; path resolution follows symlinks before containment checks; directory walking does not follow directory symlinks and filters ignored names. Listing collects all candidates before sorting so truncation reflects the globally sorted result. No writes, diffs, schemas, persistence, loop, API, or network behavior was added.

## Concerns

- Symlink behavior could not be exercised automatically on this host due to Windows permissions; the test is explicitly skipped only for that capability limitation.
- The existing suite emits a Starlette/httpx deprecation warning unrelated to this task.
