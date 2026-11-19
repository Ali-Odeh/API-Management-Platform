# Developer guide

How to work on the code: run things, test them, check them. The project's
overview and quick start are in the [README](../README.md); the design is in
[PLAN.md](PLAN.md); progress and decisions are in [ROADMAP.md](ROADMAP.md).

Commands are for PowerShell on Windows, from the repo root.

## The stack

Everything runs in Docker Compose. Create `.env` once (random secrets, never
committed), then start everything:

```powershell
.\scripts\init-env.ps1                 # macOS / Linux: ./scripts/init-env.sh
docker compose up -d --build --wait    # build, start, wait until all are healthy
docker compose ps
```

| Service | Inside Docker | From Windows | What |
| --- | --- | --- | --- |
| `gateway` | `gateway:8080` | `localhost:8081` | The Go gateway: every API call goes through it |
| `control-plane` | `control-plane:8000` | `localhost:8000` | Admin API, Swagger at `/docs` |
| `mock-backend` | `mock-backend:9000` | `localhost:9000` | Echo service to proxy to (`?delay_ms=` answers late) |
| `postgres` | `postgres:5432` | `localhost:5434` | Config database (for pgAdmin / DBeaver) |
| `redis` | `redis:6379` | `localhost:6379` | Rate-limit buckets |

Host ports 5434 and 8081 avoid clashes with local Postgres servers (5432,
5433) and another app on 8080. Containers always use the inside addresses.

```powershell
docker compose logs -f gateway         # one JSON line per request
docker compose down                    # stop, keep data
docker compose down -v                 # stop and delete the database volume
```

## Control plane (FastAPI)

The code is mounted into the container and uvicorn runs with `--reload`, so
saving a `.py` file restarts the app. At start the container applies
migrations and creates the `default` workspace and the first admin from
`ADMIN_EMAIL` / `ADMIN_PASSWORD`.

```powershell
.\backend\control-plane\scripts\check.ps1        # ruff, mypy strict, pytest (coverage >= 90%)
.\backend\control-plane\scripts\check.ps1 -Fix   # let ruff fix and format first
docker compose exec control-plane pytest -v      # tests only
```

Tests use their own database, `apim_test`, recreated on every run; the real
data in `apim` is never touched. `check.ps1 -Fix` also refreshes
`backend/control-plane/openapi.json`, the committed API contract: commit its
diff after any API change.

Migrations after changing a model (review the generated file before applying):

```powershell
docker compose exec control-plane alembic revision --autogenerate -m "describe the change"
docker compose exec control-plane alembic upgrade head
```

Log in from PowerShell:

```powershell
$body  = @{ email = "admin@example.com"; password = "<ADMIN_PASSWORD from .env>" } | ConvertTo-Json
$token = (Invoke-RestMethod -Method Post http://localhost:8000/api/v1/auth/login -ContentType 'application/json' -Body $body).access_token
$h     = @{ Authorization = "Bearer $token" }
Invoke-RestMethod http://localhost:8000/api/v1/auth/me -Headers $h
```

In Swagger (`http://localhost:8000/docs`): call `POST /api/v1/auth/login`,
copy `access_token`, click **Authorize** and paste it.

Watch the gateway's long polling (two windows; `INTERNAL_TOKEN` is in `.env`):

```powershell
# Window 1: ask like the gateway does. It waits, because nothing has changed.
$it = @{ "X-Internal-Token" = "<INTERNAL_TOKEN>" }
$etag = (Invoke-WebRequest -UseBasicParsing http://localhost:8000/internal/config -Headers $it).Headers['ETag']
Invoke-WebRequest -UseBasicParsing "http://localhost:8000/internal/config?wait=30" -Headers ($it + @{ "If-None-Match" = $etag })
# Window 2: change anything in Swagger (e.g. block a consumer). Window 1 answers at once.
```

For editor autocomplete only (optional), a local venv:

```powershell
cd backend/control-plane
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
```

## Gateway (Go)

Go has no reload: rebuild the container after a change.

```powershell
docker compose up -d --build gateway
.\backend\gateway\scripts\check.ps1        # gofmt, go vet, go test (coverage >= 90%)
.\backend\gateway\scripts\check.ps1 -Fix   # gofmt first
```

The tests run with local Go. The rate-limit tests need Docker's Redis (they
use database 15, so real buckets are untouched) and skip without
`REDIS_TEST_URL`; `check.ps1` sets it. Test packages run in parallel against
that one database, so Redis tests never `FLUSHDB` and each uses its own
consumer IDs.

The race detector needs a C compiler; run it in the Linux Go image:

```powershell
cd backend\gateway
docker run --rm -v "${PWD}:/src" -w /src golang:1.27 go test -race ./...
```

Fuzz tests and benchmarks (by hand):

```powershell
cd backend\gateway
go test -run=^$ -fuzz=FuzzMatch -fuzztime=30s ./internal/proxy
go test -run=^$ -bench=. -benchmem ./internal/proxy ./internal/middleware ./cmd/gateway
```

## End-to-end tests

The MVP story against the real stack (15 checks, about 5 s). They run in
their own container, create their own objects and always delete them:

```powershell
docker compose run --rm e2e
```

Run them for any change a request touches from end to end.

## Load test

k6 in Docker: direct vs. through the gateway, at a fixed rate (about 1 min
40 s). Details and results in [loadtest/README.md](../loadtest/README.md).

```powershell
docker compose run --rm loadtest
docker compose run --rm -e RATE=500 -e DURATION=60 loadtest
```

## CI

`.github/workflows/ci.yml` runs on every push and pull request: the control
plane checks, the gateway checks (plus `-race`, a 10 s run of every fuzz test
and the image build), the mock backend tests, then the whole stack with the
end-to-end tests and a short k6 run. Keep it in step with the `check.ps1`
scripts.

## Conventions

The rules the code follows (layers, errors, tests, commits) are in
[CLAUDE.md](../CLAUDE.md), section "Code quality". In short: small steps from
PLAN.md section 10, tests with every change, both `check.ps1` scripts green
before a commit, and the decisions log in ROADMAP.md for anything that
changes the plan.
