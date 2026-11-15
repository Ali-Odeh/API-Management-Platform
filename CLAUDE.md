# CLAUDE.md

## Project

API Management Platform, a learning project modeled on Kong, Tyk and Apigee. The full plan is in `docs/PLAN.md`. Read it before starting any task. It is the source of truth for schema, endpoints, repo layout and roadmap.

- **Gateway (data plane):** Go. Reverse proxy with route matching, API-key auth, a Redis token-bucket rate limiter and JSON logs. It never touches Postgres. It polls `/internal/config` every 5s and swaps the snapshot in with `atomic.Pointer`.
- **Control plane:** FastAPI. Admin API under `/api/v1` with JWT auth, plus `/internal/config` protected by `X-Internal-Token` with ETag/304.
- **Dashboard:** React (v2). Do not build it yet.

## Stack

- Go 1.23+, **standard library only** (`net/http`, `httputil.ReverseProxy`, `log/slog`) unless the plan says otherwise. Redis client is the exception when M4 needs it.
- Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2, pytest + httpx
- PostgreSQL 16, Redis 7, Docker Compose, k6

## Repo layout

The repo root is the plan's `api-platform/` folder.

```
gateway/           cmd/gateway/main.go, internal/{config,middleware,proxy,ratelimit}
control-plane/     app/{core,auth,services,routes,consumers,keys,gateway_config}, main.py, alembic/, tests/
                   each feature folder: router.py, models.py, schemas.py, crud.py
mock-backend/      FastAPI echo service (port 9000)
dashboard/         React (v2)
loadtest/          k6 scripts
docs/PLAN.md       the plan
docker-compose.yml
```

## Working rules

- The user is a backend engineer (FastAPI, Spring Boot, Node) and **new to Go**. The goal is learning, not just code.
- Work in **small steps: one checklist item from PLAN.md section 10 at a time**. Stop after each step so the user can review, run it and ask questions.
- For Go, **briefly explain each new Go concept the first time it appears**.
- **Write tests alongside the code** and show how to run them.
- Follow the plan's schema, endpoints and layout. **If something in the plan should change, propose it first. Don't change it silently.**
- The user is on **Windows**, so give commands for **PowerShell**. Python is run with `py` (plain `python` is the Store alias).
- Git: remote `origin` is `https://github.com/Ali-Odeh/API-Management-Platform.git`. **Never push.** The user pushes.
- Secrets live in `.env` (gitignored). Only `.env.example` is committed.
