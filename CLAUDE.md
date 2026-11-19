# CLAUDE.md

## Project

API Management Platform, a learning project modeled on Kong, Tyk and Apigee. The full plan is in `docs/PLAN.md`. Read it before starting any task. It is the source of truth for schema, endpoints, repo layout and roadmap.

- **Gateway (data plane):** Go. Reverse proxy with route matching, API-key auth, a Redis token-bucket rate limiter and JSON logs. It never touches Postgres. It long-polls `/internal/config?wait=30` (answered instantly on change via Postgres NOTIFY) and swaps the snapshot in with `atomic.Pointer`.
- **Control plane:** FastAPI. Admin API under `/api/v1` with JWT auth, plus `/internal/config` protected by `X-Internal-Token` with ETag/304.
- **Dashboard:** React (v2), in `frontend/`. Do not build it yet.

## Stack

- Go 1.23+, **standard library only** (`net/http`, `httputil.ReverseProxy`, `log/slog`) unless the plan says otherwise. Redis client is the exception when M4 needs it.
- Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2, pytest + httpx
- PostgreSQL 16, Redis 7, Docker Compose, k6

## Repo layout

The repo root is the plan's `api-platform/` folder. Services are grouped into `backend/` and `frontend/`, a change from the original plan that the user approved and that is reflected in PLAN.md section 9.

```
backend/
  gateway/         cmd/gateway/main.go, internal/{config,middleware,proxy,ratelimit,reqctx,respond,server,settings}
  control-plane/   app/{core,auth,services,routes,consumers,keys,gateway_config}, main.py, alembic/, tests/
                   each feature folder: router.py, models.py, schemas.py, crud.py
  mock-backend/    FastAPI echo service (port 9000)
frontend/          React dashboard (v2); compose service name stays `dashboard`
loadtest/          k6 scripts
docs/PLAN.md       the plan
docker-compose.yml
```

## Code quality (always)

The user expects production-style code: scalable, maintainable, readable, clean architecture.

- **Layers in the control plane:** `router.py` handles HTTP only (parse input, call crud, shape output). `crud.py` holds all DB access and business rules. `models.py` holds tables, and `schemas.py` holds input/output validation. Routers never write queries.
- `crud.py` never raises `HTTPException`. It raises `NotFoundError` / `ConflictError` from `app/core/errors.py`, which map to `{"detail": ...}` with 404/409.
- Reuse the shared core instead of copying it: mixins (`core/models.py`), `CurrentAdmin` / `DbSession` (`core/deps.py`), `Pagination` / `Page[T]` (`core/pagination.py`), errors (`core/errors.py`).
- Type hints everywhere, small functions, clear names, short docstrings that say *why*. No dead code.
- Go follows the same spirit: small packages with one responsibility, interfaces at boundaries, table-driven tests.
- Split each step into 2–3 logical commits.
- `.github/workflows/ci.yml` runs the same checks on GitHub for every push and pull request; keep it in step with `check.ps1`. `backend/control-plane/openapi.json` is the committed API contract: after an API change, run `check.ps1 -Fix` (or `scripts/export_openapi.py`) and commit the diff.
- Before every commit, `backend/control-plane/scripts/check.ps1` must pass: ruff lint and format, mypy strict on `app/`, pytest with coverage of at least 90% (currently 100%). Config lives in `backend/control-plane/pyproject.toml`.
- For gateway changes, `backend/gateway/scripts/check.ps1` must pass too: gofmt, go vet, go test. It runs with local Go on Windows (the gateway has no database). CI adds `-race` and an image build.

## Reminders for later milestones

Raise these with the user when the milestone starts (details in PLAN.md section 12, "Patterns to apply later"):

- **v2 usage events:** use the **inbox** pattern (batch ids, skip repeats) so retried batches aren't counted twice.
- **v3 webhooks and audit log:** use the **outbox** pattern (outbox row in the same transaction, a worker delivers with retries).
- **v2 `last_used_at`:** the gateway never writes to Postgres; it stays null until usage events exist. Exclude it from the config_version triggers then (it isn't config).

## Working rules

- The user is a backend engineer (FastAPI, Spring Boot, Node) and **new to Go**. The goal is learning, not just code.
- Work in **small steps: one checklist item from PLAN.md section 10 at a time**. Stop after each step so the user can review, run it and ask questions.
- **`docs/ROADMAP.md` tracks progress.** At the end of every step, update it: the step's status and commits, the overview counts, the quality numbers, "Current step", and the decisions log for any new decision.
- For Go, **briefly explain each new Go concept the first time it appears**.
- **Write tests alongside the code** and show how to run them.
- Follow the plan's schema, endpoints and layout. **If something in the plan should change, propose it first. Don't change it silently.**
- The user is on **Windows**, so give commands for **PowerShell**. Python is run with `py` (plain `python` is the Store alias).
- Git: remote `origin` is `https://github.com/Ali-Odeh/API-Management-Platform.git`. **Never push.** The user pushes.
- Secrets live in `.env` (gitignored). Only `.env.example` is committed.
- **The apps run only in Docker** (decided at step 1.3). The control plane always uses `DATABASE_URL=...@postgres:5432/...`. Run its tests and Alembic inside the container: `docker compose exec control-plane pytest` and `docker compose exec control-plane alembic ...`. The code is bind-mounted and uvicorn runs with `--reload`. The local `.venv` (Python 3.12, made with `uv`) only serves editor autocomplete. Exception (step 3.1): Go tests run locally with `go test ./...`, since the gateway has no database; the gateway itself still runs in Docker (`docker compose up -d --build gateway` after a change, as Go has no `--reload`).
- **Tenant-ready (option C, decided at step 1.4):** every config table uses `WorkspaceMixin` (`workspace_id` FK). The MVP has one `default` workspace. Every query lives in the feature's `crud.py` and filters by the current admin's `workspace_id`. Full multi-tenancy is milestone MT, after M4.
- Tables use the mixins in `app/core/models.py` (uuid id, timestamps, workspace). Protect admin endpoints with `CurrentAdmin` from `app/core/deps.py`. New models must be imported in `alembic/env.py`. Migrations are autogenerated in the container and reviewed before applying.
- Tests use the `apim_test` database, which `tests/conftest.py` recreates and migrates each run. Fixtures: `client`, `db`, `admin`, `auth_headers`.
- The user has local Windows Postgres 18 on port 5432 and 14 on 5433. The Docker Postgres is mapped to **host port 5434**, which is only for viewing data from Windows tools like pgAdmin.
- The gateway listens on 8080 inside Docker but is mapped to **host port 8081**, because another Windows app holds 127.0.0.1:8080. From Windows (PowerShell, k6) call `http://localhost:8081`; containers still use `gateway:8080`.
