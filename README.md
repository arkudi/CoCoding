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
python -m uvicorn app.main:app --app-dir backend
```

After the build, FastAPI serves the frontend from `frontend/dist` at `http://127.0.0.1:8000`, including built assets. If no build is present, the root route returns an API hint instead.

## Agent runtime and Run API

Phase one includes session-backed synchronous agent runs. Create a session for a trusted local workspace, then submit a prompt to its Run API. The response includes the persisted run, messages, tool calls, and file-change evidence.

```powershell
$session = curl.exe -sS -X POST http://127.0.0.1:8000/api/sessions `
  -H "Content-Type: application/json" `
  -d '{"title":"Local project","workspace_path":"F:\\Codes\\agent"}' | ConvertFrom-Json

curl.exe -sS -X POST "http://127.0.0.1:8000/api/sessions/$($session.id)/runs" `
  -H "Content-Type: application/json" `
  -d '{"prompt":"Inspect the project and report how to run its tests.","max_steps":20}'
```

Security boundary: agent command execution is **not sandboxed**. Use this runtime only with trusted local workspaces, and do not point a session at directories containing data or commands you would not authorize the agent to access. The runtime permits only one active agent run per process; keep production at `--workers 1`.

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

This foundation provides a local session-aware coding workspace shell, health and session APIs, a synchronous Run API backed by a DeepSeek tool-calling agent, persisted run evidence, and a production-like static frontend host. The agent can list and read workspace files, write and replace workspace files, inspect its changes, and run bounded local commands within the trusted workspace boundary.

## Dependency debt

The backend test suite currently reports Starlette's upstream `TestClient` deprecation warning about the `httpx` compatibility layer. The supported FastAPI/Starlette dependency set does not yet provide the suggested replacement package, so the warning remains visible rather than being filtered or hidden.
