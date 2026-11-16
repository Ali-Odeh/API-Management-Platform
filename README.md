# API Management Platform

A self-hosted API management system, built as a learning project: a **Go gateway** that every API request passes through (auth, rate limiting, logging), a **FastAPI control plane** that configures it, and later a React dashboard.

The full design is in [docs/PLAN.md](docs/PLAN.md).

## Status

Milestone **M1: Skeleton** is done ✅. Postgres, Redis, the mock backend and the control plane run in Docker Compose. The control plane has admin login (JWT), services CRUD, and a tenant-ready data model (one `default` workspace). Next: **M2**, the rest of the control plane.

## Getting started (Windows / PowerShell)

Requirements: Docker Desktop, Python 3.12+, Go 1.23+.

```powershell
Copy-Item .env.example .env   # then fill in the values
docker compose up -d
docker compose ps
```

Check that the mock backend is reachable:

```powershell
Invoke-RestMethod http://localhost:9000/anything/hello
```

### Control plane

Everything runs inside Docker. The code folder is mounted into the container, so saving a `.py` file restarts the app automatically.

```powershell
Invoke-RestMethod http://localhost:8000/health        # {"status":"ok"}
start http://localhost:8000/docs                       # Swagger UI
docker compose exec control-plane pytest -v            # run the tests
docker compose exec control-plane alembic current      # migration status
docker compose logs -f control-plane                   # follow the logs
```

When the container starts it runs `alembic upgrade head` (applies migrations) and creates the `default` workspace and the first admin from `ADMIN_EMAIL` / `ADMIN_PASSWORD` if they don't exist yet.

Log in and call a protected endpoint:

```powershell
$body  = @{ email = "admin@example.com"; password = "<ADMIN_PASSWORD from .env>" } | ConvertTo-Json
$token = (Invoke-RestMethod -Method Post http://localhost:8000/api/v1/auth/login -ContentType 'application/json' -Body $body).access_token
Invoke-RestMethod http://localhost:8000/api/v1/auth/me -Headers @{ Authorization = "Bearer $token" }
```

In Swagger (`/docs`): call `POST /api/v1/auth/login`, copy `access_token`, click **Authorize**, paste it.

Register a backend service (needs the token from above):

```powershell
$h = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/services -Headers $h -ContentType 'application/json' `
  -Body '{"name":"orders","upstream_url":"http://mock-backend:9000"}'
Invoke-RestMethod "http://localhost:8000/api/v1/services?page=1&page_size=20" -Headers $h
```

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/auth/login`, `GET /api/v1/auth/me` | Admin login and current admin |
| `GET, POST /api/v1/services` | List (paginated `{items, total}`), create |
| `GET, PATCH, DELETE /api/v1/services/{id}` | Read, partial update, delete |

New migration after changing a model:

```powershell
docker compose exec control-plane alembic revision --autogenerate -m "describe the change"
docker compose exec control-plane alembic upgrade head
```

Tests run against a separate database, `apim_test`, which is recreated on every test run. Your real data in `apim` is never touched.

Run every quality check before committing (ruff lint and format, mypy strict, pytest with coverage of at least 90%):

```powershell
.\backend\control-plane\scripts\check.ps1        # check only
.\backend\control-plane\scripts\check.ps1 -Fix   # let ruff fix and format first
```

For editor autocomplete only (optional), create a local venv:

```powershell
cd backend/control-plane
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
```

### Database

Connect to the database from Windows (pgAdmin, DBeaver, psql): `localhost:5434`, database, user and password from `.env`. Port 5434 is used on the host so it does not clash with locally installed Postgres servers on 5432 and 5433. Inside Docker, containers still use `postgres:5432`.

Stop everything:

```powershell
docker compose down        # keep data
docker compose down -v     # also delete the Postgres volume
```

## Layout

| Folder | What |
| --- | --- |
| `backend/gateway/` | Go reverse proxy (data plane) |
| `backend/control-plane/` | FastAPI admin API (control plane) |
| `backend/mock-backend/` | Small FastAPI echo service to proxy to |
| `frontend/` | React admin UI (v2, not started) |
| `loadtest/` | k6 scripts |
| `docs/` | Plan and architecture |
