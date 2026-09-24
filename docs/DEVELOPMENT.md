# Local development

The project currently contains the FastAPI and React/TypeScript foundations and
the DynamoDB-backed application domain. Hosted authentication, Gmail access,
and AI providers are not implemented yet.

## Prerequisites

- Python 3.12.13, pinned in [`.python-version`](../.python-version)
- [`uv`](https://docs.astral.sh/uv/) for Python dependencies
- Node.js 22.23.3, pinned in [`.nvmrc`](../.nvmrc)
- npm 10.9.9, included with the pinned Node.js release

Milestones 0 and 1 need no credentials or environment variables. Future local
values belong in `.env`, which Git ignores. Commit only placeholder values to
`.env.example`.

## Run the backend

```bash
cd backend
uv sync --locked --dev
uv run uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`. Verify it with:

```bash
curl http://127.0.0.1:8000/health
```

The expected response is `{"status":"ok"}`.

## Run the frontend

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Vite prints the local URL when it starts, normally `http://127.0.0.1:5173`.

## Run all checks

Backend:

```bash
cd backend
uv sync --locked --dev
uv run ruff check .
uv run pytest -q
```

Frontend:

```bash
cd frontend
npm ci
npm audit
npm run lint
npm test
npm run typecheck
npm run build
```

GitHub Actions runs these same commands for pushes and pull requests.

## Application domain

Application CRUD uses the `APPLICATIONS_TABLE` and `EVENTS_TABLE` DynamoDB
tables and the optional `AWS_REGION` setting. Automated tests create these
tables with `moto`; they do not use an AWS account.

The authentication dependency intentionally returns `401` until Cognito is
implemented. API tests inject synthetic user subjects through FastAPI dependency
overrides. Do not add a request header that lets callers choose an owner.
