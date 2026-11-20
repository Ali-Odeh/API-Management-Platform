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
| `auth` | `auth:9999` | `localhost:9999` | GoTrue: login, MFA, refresh, invitations |
| `mailpit` | `mailpit:1025` (SMTP) | `localhost:8025` | Catches every email GoTrue sends (web UI) |
| `postgres` | `postgres:5432` | `localhost:5434` | Databases `apim` (config) and `auth` (GoTrue's own), for pgAdmin / DBeaver |
| `redis` | `redis:6379` | `localhost:6379` | Rate-limit buckets |

Host ports 5434 and 8081 avoid clashes with local Postgres servers (5432,
5433) and another app on 8080. Containers always use the inside addresses.
A one-shot `auth-db-init` job prepares GoTrue's database on every `up` and
exits (it shows as "Exited (0)" in `docker compose ps -a`).

```powershell
docker compose logs -f gateway         # one JSON line per request
docker compose down                    # stop, keep data
docker compose down -v                 # stop and delete the database volume
```

## Control plane (FastAPI)

The code is mounted into the container and uvicorn runs with `--reload`, so
saving a `.py` file restarts the app. At start the container applies
migrations and makes sure the `default` workspace and the first Platform
Admin exist (in GoTrue too, from `PLATFORM_ADMIN_EMAIL` /
`PLATFORM_ADMIN_PASSWORD`; an existing account keeps its password).

```powershell
.\backend\control-plane\scripts\check.ps1        # ruff, mypy strict, pytest (coverage >= 90%)
.\backend\control-plane\scripts\check.ps1 -Fix   # let ruff fix and format first
docker compose exec control-plane pytest -v      # tests only
```

Tests use their own database, `apim_test`, recreated on every run; the real
data in `apim` is never touched. They need no GoTrue: they sign tokens with
the GoTrue secret (`tests/fakes.py`) and use an in-memory fake of its admin API. `check.ps1 -Fix` also refreshes
`backend/control-plane/openapi.json`, the committed API contract: commit its
diff after any API change.

Migrations after changing a model (review the generated file before applying):

```powershell
docker compose exec control-plane alembic revision --autogenerate -m "describe the change"
docker compose exec control-plane alembic upgrade head
```

Log in from PowerShell. People log in with GoTrue (`localhost:9999`), never
with the control plane; the Platform Admin is `PLATFORM_ADMIN_EMAIL` /
`PLATFORM_ADMIN_PASSWORD` from `.env`:

```powershell
$gt    = "http://localhost:9999"
$pw    = (Select-String -Path .env -Pattern '^PLATFORM_ADMIN_PASSWORD=(.*)').Matches.Groups[1].Value
$login = Invoke-RestMethod -Method Post "$gt/token?grant_type=password" -ContentType application/json `
           -Body (@{ email = "admin@example.com"; password = $pw } | ConvertTo-Json)
$h     = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod http://localhost:8000/api/v1/me -Headers $h
```

That token (`aal1`, password only) opens the service, route, consumer and key
endpoints. Platform endpoints (`/api/v1/users/...`) also need MFA (`aal2`),
else `403 mfa_required`. Add a TOTP factor once, then step up after each
password login:

```powershell
# Once: enroll, and add the secret (or $f.totp.uri) to an authenticator app.
$f = Invoke-RestMethod -Method Post "$gt/factors" -Headers $h -ContentType application/json `
       -Body '{"factor_type":"totp","friendly_name":"my phone"}'
$f.totp.secret

# After each password login: challenge, then verify the app's 6-digit code.
$factor = (Invoke-RestMethod "$gt/user" -Headers $h).factors | Select-Object -First 1
$c   = Invoke-RestMethod -Method Post "$gt/factors/$($factor.id)/challenge" -Headers $h -ContentType application/json -Body '{}'
$mfa = Invoke-RestMethod -Method Post "$gt/factors/$($factor.id)/verify" -Headers $h -ContentType application/json `
         -Body (@{ challenge_id = $c.id; code = "123456" } | ConvertTo-Json)
$h   = @{ Authorization = "Bearer $($mfa.access_token)" }
```

Access tokens last 10 minutes; log in again (or use `refresh_token` with
`grant_type=refresh_token`) after that. In Swagger (`http://localhost:8000/docs`):
click **Authorize** and paste an access token.

Emails GoTrue sends (invitations, password recovery) land in Mailpit:
`http://localhost:8025`.

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

The MVP and MT stories against the real stack (21 checks, about 6 s): GoTrue
login with password and TOTP, a disabled person locked out, then requests
through the gateway. They run in their own container, create their own
objects and always delete them. For MFA they enroll their own factor named
`e2e` on the Platform Admin and remove it again, so a factor you enrolled is
left alone; their test person `e2e-person@example.test` is reused by every run.

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
[AGENTS.md](../AGENTS.md), for people and coding agents alike. In short: one
roadmap step at a time, tests with every change, both `check.ps1` scripts and
the e2e tests green before handing back, and the decisions log in ROADMAP.md
for anything that changes the plan.
