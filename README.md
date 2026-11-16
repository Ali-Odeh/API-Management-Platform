# API Management Platform

A self-hosted API management system, built as a learning project: a **Go gateway** that every API request passes through (auth, rate limiting, logging), a **FastAPI control plane** that configures it, and later a React dashboard.

The full design is in [docs/PLAN.md](docs/PLAN.md).

## Status

Milestone **M1: Skeleton** is in progress. Postgres, Redis, the mock backend and the control plane base (FastAPI + async SQLAlchemy + Alembic) run in Docker Compose.

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
