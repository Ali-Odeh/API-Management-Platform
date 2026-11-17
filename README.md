# API Management Platform

[![ci](https://github.com/Ali-Odeh/API-Management-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Ali-Odeh/API-Management-Platform/actions/workflows/ci.yml)

A self-hosted API management system, built as a learning project: a **Go gateway** that every API request passes through (auth, rate limiting, logging), a **FastAPI control plane** that configures it, and later a React dashboard.

The full design is in [docs/PLAN.md](docs/PLAN.md). Step-by-step progress is in [docs/ROADMAP.md](docs/ROADMAP.md).

## Status

Milestones **M1: Skeleton** and **M2: Control plane** are done ✅. The control plane has admin login (JWT), CRUD for services, routes, consumers and API keys, a tenant-ready data model (one `default` workspace), and `/internal/config` for the gateway with ETag/304 and long polling. Next: **M3**, the Go gateway.

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
| `GET, PATCH, DELETE /api/v1/services/{id}` | Read, partial update, delete (409 while it has routes) |
| `GET, POST /api/v1/routes` | List (`?service_id=`), create |
| `GET, PATCH, DELETE /api/v1/routes/{id}` | Read, partial update, delete |
| `GET, POST /api/v1/consumers` | List (`?status=active\|blocked`), create |
| `GET, PATCH, DELETE /api/v1/consumers/{id}` | Read, update (block with `{"status":"blocked"}`), delete (also deletes its keys) |
| `GET, POST /api/v1/consumers/{id}/keys` | List keys (prefix only), generate a key (the full key is shown **once**) |
| `DELETE /api/v1/keys/{id}` | Revoke a key (kept in the list as revoked) |
| `GET /internal/config` | Gateway only (`X-Internal-Token`): config snapshot, `ETag`/`304`, long polling with `?wait=` |

Watch the gateway's long polling yourself (two PowerShell windows; `INTERNAL_TOKEN` is in `.env`):

```powershell
# Window 1: ask like the gateway does. It waits, because nothing has changed yet.
$it = @{ "X-Internal-Token" = "<INTERNAL_TOKEN>" }
$etag = (Invoke-WebRequest -UseBasicParsing http://localhost:8000/internal/config -Headers $it).Headers['ETag']
Invoke-WebRequest -UseBasicParsing "http://localhost:8000/internal/config?wait=30" -Headers ($it + @{ "If-None-Match" = $etag })
# Window 2: change anything in Swagger (e.g. block a consumer). Window 1 answers at once.
```

New migration after changing a model:

```powershell
docker compose exec control-plane alembic revision --autogenerate -m "describe the change"
docker compose exec control-plane alembic upgrade head
```

Tests run against a separate database, `apim_test`, which is recreated on every test run. Your real data in `apim` is never touched.

Run every quality check before committing (ruff lint and format, mypy strict, pytest with coverage of at least 90%, and the OpenAPI contract in `backend/control-plane/openapi.json`). GitHub Actions runs the same checks on every push and pull request (`.github/workflows/ci.yml`):

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
