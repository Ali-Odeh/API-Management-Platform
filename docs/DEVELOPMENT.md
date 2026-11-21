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
| `audit-retention` | — | — | Deletes audit events older than `AUDIT_RETENTION_DAYS` (default 365), at start and daily |
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

### Upgrading an MVP install (keep your data)

Before MT.1 the admin logged in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`; now
GoTrue does, with new secrets in `.env`. The new `docker-compose.yml` needs
those secrets for **every** command (even `docker compose down`), so prepare
the new `.env` first, while the old stack may still be running. Keep the
Postgres values (the volume was created with that user and password, and
keeps them), take the new secrets from `init-env`, and make the old admin the
Platform Admin. The old file stays as `.env.mvp` until the upgrade is checked.

1. Prepare the new settings:

   ```powershell
   Rename-Item .env .env.mvp               # the backup: keep it until step 3 passes
   .\scripts\init-env.ps1                  # a new .env; note the printed password
   $old  = @{}
   Get-Content .env.mvp | Where-Object { $_ -match '^([A-Z_]+)=(.*)$' } | ForEach-Object { $old[$Matches[1]] = $Matches[2] }
   $keep = 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB', 'DATABASE_URL', 'INTERNAL_TOKEN'
   $lines = Get-Content .env | ForEach-Object {
       if ($_ -match '^([A-Z_]+)=' -and $keep -contains $Matches[1]) { "$($Matches[1])=$($old[$Matches[1]])" }
       elseif ($_ -match '^PLATFORM_ADMIN_EMAIL=') { "PLATFORM_ADMIN_EMAIL=$($old['ADMIN_EMAIL'])" }
       else { $_ }
   }
   [IO.File]::WriteAllText("$PWD\.env", ($lines -join "`n") + "`n", (New-Object Text.UTF8Encoding $false))
   docker compose config --quiet           # no output: the new Compose file accepts it
   ```

2. Restart on the new code (no `-v` anywhere: the volume and its data stay):

   ```powershell
   docker compose down
   docker compose up -d --build --wait
   ```

3. Check that the upgraded stack works: the Platform Admin logs in through
   GoTrue and is the migrated admin (same id as before):

   ```powershell
   $email = (Select-String -Path .env -Pattern '^PLATFORM_ADMIN_EMAIL=(.*)').Matches.Groups[1].Value
   $pw    = (Select-String -Path .env -Pattern '^PLATFORM_ADMIN_PASSWORD=(.*)').Matches.Groups[1].Value
   $login = Invoke-RestMethod -Method Post "http://localhost:9999/token?grant_type=password" -ContentType application/json `
              -Body (@{ email = $email; password = $pw } | ConvertTo-Json)
   Invoke-RestMethod http://localhost:8000/api/v1/me -Headers @{ Authorization = "Bearer $($login.access_token)" }
   docker compose logs control-plane | Select-String "is bound to GoTrue account"   # no output: no binding conflict
   ```

4. Only now delete the backup (it holds the old secrets):

   ```powershell
   Remove-Item .env.mvp
   ```

If step 2 or 3 fails, `.env.mvp` still has the old settings; `docker compose
logs control-plane auth` shows why.

What happens on start: the migration turns `admins` into `users` (same id,
email and timestamps, now Platform Admin; the old password hash is dropped,
since GoTrue hashes differently). The control plane then creates the GoTrue
account (password = the new `PLATFORM_ADMIN_PASSWORD`; set any 12+ characters
before step 2 if you prefer) and links the migrated row to it, because the
row has no GoTrue id yet. Services, routes, consumers and keys are untouched.
`tests/test_mvp_upgrade.py` checks all of this on a populated MVP database.

### Rebinding a person to another GoTrue account

A person's row stays bound to the GoTrue account it was first linked to;
nothing rebinds it automatically, because a matching email doesn't prove it
is the same person. If GoTrue's database was reset (or the account deleted
and made again), the old binding no longer fits: that account gets
`403 account_conflict`, and at every start the control plane logs an `ERROR`
naming both ids. Once you are sure the new account is right, rebind on purpose:

```powershell
docker compose exec control-plane python -m scripts.relink_user admin@example.com
```

For a disabled user, the command confirms the GoTrue ban on the replacement
account before saving the new binding. If GoTrue cannot confirm the ban, the
old binding stays in place and the command can be retried.

### Upgrading an MT.2 install: the database logins (MT.3)

Since MT.3 the API connects as its own login, `apim_app`, which may only add
and read audit events. In an existing `.env`, the old `DATABASE_URL` (the
owner) becomes `MIGRATION_DATABASE_URL`, and two logins with new passwords
are added by the `control-plane-db-init` job.

```powershell
Copy-Item .env .env.mt2                  # a backup until the stack is healthy again
function New-Password {
    $bytes = New-Object byte[] 16
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $_.ToString("x2") })
}
$old   = (Select-String -Path .env -Pattern '^DATABASE_URL=(.*)').Matches.Groups[1].Value
$db    = $old.Split('/')[-1]
$lines = Get-Content .env | ForEach-Object {
    if ($_ -match '^DATABASE_URL=') {
        "MIGRATION_DATABASE_URL=$old"
        "DATABASE_URL=postgresql+asyncpg://apim_app:$(New-Password)@postgres:5432/$db"
        "AUDIT_RETENTION_DATABASE_URL=postgresql+asyncpg://apim_retention:$(New-Password)@postgres:5432/$db"
        "AUDIT_RETENTION_DAYS=365"
    } else { $_ }
}
[IO.File]::WriteAllText("$PWD\.env", ($lines -join "`n") + "`n", (New-Object Text.UTF8Encoding $false))
docker compose up -d --build --wait      # then: Remove-Item .env.mt2
```

## Control plane (FastAPI)

Three database logins (PLAN.md section 5): the API uses `apim_app`
(`DATABASE_URL`), which can't change or delete audit events; migrations and
the startup setup (`scripts/prepare_database.py`) run in the one-shot
`control-plane-db-init` job with `MIGRATION_DATABASE_URL`. The API container
gets neither the owner nor the retention credentials. The `audit-retention`
container uses `apim_retention`. Run a retention round by hand with:

```powershell
docker compose exec audit-retention python -m scripts.audit_retention --once
```

The code is mounted into the container and uvicorn runs with `--reload`, so
saving a `.py` file restarts the app. The database setup job applies
migrations before the API starts. The API makes sure the `default` workspace and the first Platform
Admin exist (in GoTrue too, from `PLATFORM_ADMIN_EMAIL` /
`PLATFORM_ADMIN_PASSWORD`; an existing account keeps its password).

```powershell
.\backend\control-plane\scripts\check.ps1        # ruff, mypy strict, pytest (coverage >= 90%)
.\backend\control-plane\scripts\check.ps1 -Fix   # let ruff fix and format first
docker compose run --rm control-plane-db-init pytest -v   # tests only
```

Tests use their own database, `apim_test`, recreated on every run; the real
data in `apim` is never touched. They need no GoTrue: they sign tokens with
the GoTrue secret (`tests/fakes.py`) and use an in-memory fake of its admin API. `check.ps1 -Fix` also refreshes
`backend/control-plane/openapi.json`, the committed API contract: commit its
diff after any API change.

Migrations after changing a model (review the generated file before applying):

```powershell
docker compose run --rm control-plane-db-init alembic revision --autogenerate -m "describe the change"
docker compose run --rm control-plane-db-init alembic upgrade head
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

The MVP and MT stories against the real stack (26 checks, about 7 s): GoTrue
login with password and TOTP, a disabled person locked out, workspaces and
their isolation, the audit log, then requests through the gateway. They run in their own container, create their own
objects and always delete them. For MFA they enroll their own factor named
`e2e` on the Platform Admin and remove it again, so a factor you enrolled is
left alone; their test person `e2e-person@example.test` and their workspace
`e2e-tenant` are reused by every run (workspaces can be deleted only from MT.5).

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
