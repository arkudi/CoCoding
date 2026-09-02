# CoCoding

CoCoding is a local development foundation with a FastAPI backend and a Vue frontend. The API is available at `http://127.0.0.1:8000`; during frontend development, Vite runs at `http://127.0.0.1:5173` and proxies API requests to the backend.

## Prerequisites

- Python 3.11 or later
- Node.js 24.15 or later and npm (the committed lockfile is supported on this runtime)

## Backend setup

From the repository root, create and activate a virtual environment, then install the backend and its development dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Create a local environment file and put a **newly rotated** DeepSeek key in it. The old exposed key must be revoked with DeepSeek; do not copy it into this project or reuse it. Keep `.env` local and never commit it.

```powershell
Copy-Item .env.example .env
# Put a newly rotated DeepSeek key in DEEPSEEK_API_KEY inside .env
python -m uvicorn app.main:app --app-dir backend --reload
```

Reload mode runs one process by default. For a production-style server, use exactly one worker because agent runs are serialized in-process:

```powershell
python -m uvicorn app.main:app --app-dir backend --workers 1
```

## Frontend setup

Install dependencies and start the Vite development server:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173` while both the backend and Vite servers are running.

## Development

Run the backend in one PowerShell window and the Vite server in another. Vite proxies `/api` requests to `http://127.0.0.1:8000`, so frontend code can use the API without additional local CORS configuration.

## Production-like local run

Build the frontend, return to the repository root, then run FastAPI without reload:

```powershell
Set-Location frontend
npm run build

Set-Location ..
python -m uvicorn app.main:app --app-dir backend --workers 1
```

After the build, FastAPI serves the frontend from `frontend/dist` at `http://127.0.0.1:8000`, including built assets. If no build is present, the root route returns an API hint instead.

## Agent runtime and realtime Run API

The application accepts a session-backed Run with `202 Accepted`, executes it in one background worker, and publishes committed state changes through WebSocket. The Vue interface follows those events, then reconciles final messages, tool calls, and file-change evidence from SQLite.

```powershell
$session = curl.exe -sS -X POST http://127.0.0.1:8000/api/sessions `
  -H "Content-Type: application/json" `
  -d '{"title":"Local project","workspace_path":"F:\\Codes\\agent"}' | ConvertFrom-Json

curl.exe -sS -X POST "http://127.0.0.1:8000/api/sessions/$($session.id)/runs" `
  -H "Content-Type: application/json" `
  -d '{"prompt":"Inspect the project and report how to run its tests."}'
```

The returned Run initially has status `running`. Follow `ws://127.0.0.1:8000/api/runs/{run_id}/events`, or query `GET /api/runs/{run_id}`. Run history is available from `GET /api/sessions/{session_id}/runs`.

Multi-agent orchestration is enabled by default. A read-only Manager can delegate
bounded subtasks to an Explorer, an Implementer, or an independent Reviewer. Only
the Implementer receives write and general command tools. Independent Explorer and
Reviewer tasks can be batched in parallel; parallel Reviewers do not receive the
potentially mutating test runner, and Implementers remain serial. Every implementation
must receive an explicit Reviewer approval before completion. Parent/child executions,
Task DAG dependencies, and per-Agent tool ownership are persisted and shown live.
All agents share limits for model turns, estimated input tokens, tool calls,
delegations, and wall-clock duration.
Configure this with `COCODING_AGENT_MULTI_AGENT_ENABLED`,
`COCODING_AGENT_MAX_DELEGATIONS`, `COCODING_AGENT_CHILD_STEP_LIMIT`,
`COCODING_AGENT_TOKEN_BUDGET`, `COCODING_AGENT_TOOL_CALL_LIMIT`, and
`COCODING_AGENT_WALL_CLOCK_LIMIT_SECONDS`.

The model submits a structured `finish_task` call containing its summary, changed files,
tests, and unresolved issues. The runtime verifies file claims against a full workspace
baseline and test claims against recorded command results. This includes files created,
modified, deleted, or renamed by commands. Unsupported claims are returned to the model
for correction instead of completing the Run; plain text alone cannot finish a Run.

Visible assistant text is streamed through `assistant.started`, `assistant.delta`, and `assistant.finished` WebSocket events. Deltas are
transient UI state; complete assistant messages and terminal Run state remain
the durable SQLite record. Tool-call arguments and model reasoning are never
streamed to the interface.

Cancellation uses `POST /api/runs/{run_id}/cancel`. It is cooperative: the current DeepSeek request or tool call finishes first, and the Agent stops at the next safe boundary. Cancellation does not undo completed file writes; inspect their Diff in the workspace panel.

The Agent decides when work is complete by submitting `finish_task`; clients do not
choose a turn count. The runtime retains a safety-only hard limit of 50 model turns to
prevent an unbounded loop. Override it with `COCODING_AGENT_HARD_STEP_LIMIT` when needed.

Completion verification is controlled by server-side policy. By default, code changes
need successful test evidence or an explicit `verification_note`, the latest failed
`run_tests` result must be disclosed as unresolved, and incomplete acceptance checks
must be disclosed. Operators can tighten policy with
`COCODING_AGENT_ALLOW_UNVERIFIED_CODE_WITH_REASON=false`; the related
`COCODING_AGENT_REQUIRE_CODE_VERIFICATION` and
`COCODING_AGENT_REQUIRE_RESOLVED_TEST_FAILURES` flags are also configurable.

Security boundary: agent command execution is **not sandboxed**. Commands start with the session workspace as their current directory, but they retain the host user's filesystem and process access; file-tool path containment does not contain commands. Use this runtime only with trusted local workspaces and prompts. The runtime permits only one active agent run per process; keep production at `--workers 1`.

## Tests

Run the backend test suite from the repository root:

```powershell
python -m pytest backend/tests -v
```

Run the frontend tests and build from the frontend directory:

```powershell
Set-Location frontend
npm test
npm run build
```

Run the deterministic agent evaluation suite with a configured DeepSeek key:

```powershell
python -m app.evals backend/eval_suites/coding_baseline.json
python -m app.evals backend/eval_suites/orchestration_comparison.json
```

Evaluation cases run in isolated temporary workspaces and report status, step count,
tool calls, tool failures, agent roles, orchestration mode, file assertions,
response assertions, latency, and an overall pass/fail result as JSON. Reports
also aggregate average steps and tool failures by single- versus multi-agent mode.
Add cases to the suite before changing agent
behavior so improvements can be compared against a stable baseline.

## Configuration

Application configuration uses the `COCODING_` prefix, including `COCODING_DATABASE_URL` and `COCODING_FRONTEND_DIST`. DeepSeek configuration uses the unprefixed `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` names shown in `.env.example`. A local `.env` file is ignored by Git and may hold machine-specific settings; never commit credentials. `.env.example` intentionally leaves `DEEPSEEK_API_KEY` blank.

## Current scope

CoCoding now provides a local session-aware Vue workspace, a background Run API backed by a DeepSeek tool-calling agent, WebSocket execution events, cooperative cancellation, persisted Run history, tool evidence, safe text preview, unified Diff display, and a production-like static frontend host. The Agent can list, search, and read workspace files; write, replace, and apply validated multi-file text patches; inspect workspace and Git changes; run bounded commands; and invoke recognized test runners with structured result counts. Commands retain the host user's filesystem and process access.

## Dependency debt

The backend test suite currently reports Starlette's upstream `TestClient` deprecation warning about the `httpx` compatibility layer. The supported FastAPI/Starlette dependency set does not yet provide the suggested replacement package, so the warning remains visible rather than being filtered or hidden.
