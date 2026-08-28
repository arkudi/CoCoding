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

Start the backend with reload enabled:

```powershell
python -m uvicorn app.main:app --app-dir backend --reload
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

Application configuration uses the `COCODING_` prefix, including `COCODING_DATABASE_URL` and `COCODING_FRONTEND_DIST`. DeepSeek configuration uses the unprefixed `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` names shown in `.env.example`. A local `.env` file is ignored by Git and may hold machine-specific settings; do not commit credentials.

## Current scope

This foundation provides a local session-aware coding workspace shell, health and session APIs, and a production-like static frontend host. Vue Router task pages, the mobile workspace drawer, and the Agent runtime are future work; this slice does not yet execute coding tasks.

## Dependency debt

The backend test suite currently reports Starlette's upstream `TestClient` deprecation warning about the `httpx` compatibility layer. The supported FastAPI/Starlette dependency set does not yet provide the suggested replacement package, so the warning remains visible rather than being filtered or hidden.
