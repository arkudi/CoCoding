# CoCoding

CoCoding is a local development foundation with a FastAPI backend and a Vue frontend. The API is available at `http://127.0.0.1:8000`; during frontend development, Vite runs at `http://127.0.0.1:5173` and proxies API requests to the backend.

## Prerequisites

- Python 3.11 or later
- Node.js and npm

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

Configuration uses environment variables prefixed with `COCODING_`. Useful values include `COCODING_DATABASE_URL`, `COCODING_FRONTEND_DIST`, and `DEEPSEEK_API_KEY`. A local `.env` file is ignored by Git and may hold machine-specific settings; do not commit credentials.

## Current scope

This foundation provides a local session-aware coding workspace shell, health and session APIs, and a production-like static frontend host. It does not yet execute coding tasks or include Agent runtime features.
