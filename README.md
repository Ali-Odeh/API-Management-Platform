# API Management Platform

A self-hosted API management system, built as a learning project: a **Go gateway** that every API request passes through (auth, rate limiting, logging), a **FastAPI control plane** that configures it, and later a React dashboard.

The full design is in [docs/PLAN.md](docs/PLAN.md).

## Status

Milestone **M1: Skeleton** is in progress. Postgres, Redis and the mock backend run in Docker Compose.

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

Stop everything:

```powershell
docker compose down        # keep data
docker compose down -v     # also delete the Postgres volume
```

## Layout

| Folder | What |
| --- | --- |
| `gateway/` | Go reverse proxy (data plane) |
| `control-plane/` | FastAPI admin API (control plane) |
| `mock-backend/` | Small FastAPI echo service to proxy to |
| `dashboard/` | React admin UI (v2, not started) |
| `loadtest/` | k6 scripts |
| `docs/` | Plan and architecture |
