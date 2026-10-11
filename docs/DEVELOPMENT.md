# Local development

The project currently contains the manual tracker: a FastAPI backend persisting
to DynamoDB and a React/TypeScript dashboard for creating, editing, filtering,
and inspecting applications. Hosted authentication, Gmail access, and AI
providers are not implemented yet.

## Prerequisites

- Python 3.12.13, pinned in [`.python-version`](../.python-version)
- [`uv`](https://docs.astral.sh/uv/) for Python dependencies
- Node.js 22.23.3, pinned in [`.nvmrc`](../.nvmrc)
- npm 10.9.9, included with the pinned Node.js release
- Docker Desktop, for DynamoDB Local

Local development uses the fake values in `.env.example`, copied to `.env`,
which Git ignores. Real credentials belong only in `.env`; commit only
placeholder values to `.env.example`.

## Run the app locally

```bash
cp .env.example .env             # fake local values only
docker compose up -d dynamodb    # DynamoDB Local on 127.0.0.1:8001

cd backend
uv sync --locked --dev
uv run --env-file ../.env python -m scripts.create_local_tables
uv run --env-file ../.env uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open the URL Vite prints, normally `http://localhost:5173`. Vite proxies `/api`
requests to the API on `http://127.0.0.1:8000`. Data persists in the
`dynamodb-data` Docker volume; `docker compose down -v` deletes it.

Check the API directly with `curl http://127.0.0.1:8000/health`; the expected
response is `{"status":"ok"}`.

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

The authentication dependency returns `401` until Cognito is implemented in
milestone 4. For local development, setting the server-side `LOCAL_DEV_SUBJECT`
variable authenticates requests from loopback addresses (`127.0.0.1`, `::1`) as
that subject; it is ignored for other clients and refused inside AWS Lambda.
API tests inject synthetic user subjects through FastAPI dependency overrides.
Do not add a request header that lets callers choose an owner.
