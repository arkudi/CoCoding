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
  -d '{"prompt":"Inspect the project and report how to run its tests.","max_steps":20}'
```

The returned Run initially has status `running`. Follow `ws://127.0.0.1:8000/api/runs/{run_id}/events`, or query `GET /api/runs/{run_id}`. Run history is available from `GET /api/sessions/{session_id}/runs`.

Visible assistant text is streamed through `assistant.started`, `assistant.delta`, and `assistant.finished` WebSocket events. Deltas are
transient UI state; complete assistant messages and terminal Run state remain
the durable SQLite record. Tool-call arguments and model reasoning are never
streamed to the interface.

Cancellation uses `POST /api/runs/{run_id}/cancel`. It is cooperative: the current DeepSeek request or tool call finishes first, and the Agent stops at the next safe boundary. Cancellation does not undo completed file writes; inspect their Diff in the workspace panel.

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

## Configuration

Application configuration uses the `COCODING_` prefix, including `COCODING_DATABASE_URL` and `COCODING_FRONTEND_DIST`. DeepSeek configuration uses the unprefixed `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` names shown in `.env.example`. A local `.env` file is ignored by Git and may hold machine-specific settings; never commit credentials. `.env.example` intentionally leaves `DEEPSEEK_API_KEY` blank.

## Current scope

CoCoding now provides a local session-aware Vue workspace, a background Run API backed by a DeepSeek tool-calling agent, WebSocket execution events, cooperative cancellation, persisted Run history, tool evidence, safe text preview, unified Diff display, and a production-like static frontend host. The Agent can list and read workspace files, write and replace workspace files, inspect its changes, and run bounded local commands with the workspace as their current directory. Those commands retain the host user's filesystem and process access.

## Dependency debt

The backend test suite currently reports Starlette's upstream `TestClient` deprecation warning about the `httpx` compatibility layer. The supported FastAPI/Starlette dependency set does not yet provide the suggested replacement package, so the warning remains visible rather than being filtered or hidden.
