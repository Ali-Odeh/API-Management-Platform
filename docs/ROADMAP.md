# Roadmap and progress

Where the project stands, step by step. The *what* and *why* of each milestone live in [PLAN.md](PLAN.md) section 10. This file tracks *progress*, and it is updated at the end of every step.

**Last updated:** Nov 18, 2026 · **Current step:** 3.2 (config long-poll loop)

## Overview

| Milestone | Progress | Status |
| --- | --- | --- |
| Step 0: Go basics | ⬜⬜⬜⬜ 0/4 | 🔄 Ali, in parallel (A Tour of Go) |
| M1: Skeleton | ✅✅✅✅✅✅ 6/6 | ✅ Done |
| M2: Control plane done | ✅✅✅✅✅✅ 6/6 | ✅ Done |
| M3: Gateway proxies (Go) | ✅⬜⬜⬜⬜⬜⬜ 1/7 | 🔄 In progress |
| M4: MVP done | ⬜⬜⬜⬜⬜ 0/5 | ⏳ |
| MT: Multi-tenancy | ⬜⬜⬜⬜⬜⬜ 0/6 | ⏳ After the MVP |
| v2a: Admin UI | ⬜⬜⬜ 0/3 | ⏳ After the MVP |

**MVP:** 13 of 24 steps done (54%). First Go code is running.

## Quality today

**Control plane (Python)**

| Check | Result |
| --- | --- |
| Tests | 326 passing (about 35–65 s) |
| Coverage | 100% (minimum 90%) |
| ruff lint and format | clean |
| mypy strict | clean |
| Run all checks | `.\backend\control-plane\scripts\check.ps1` (`-Fix` to autofix) |

**Gateway (Go)**

| Check | Result |
| --- | --- |
| Tests | 31 passing (10 tests, 21 of them table cases), about 3 s |
| Coverage | settings 100%, server 97.4%, main 62.1% (`serve()` waits for a real signal; checked in Docker instead) |
| gofmt, go vet | clean |
| Image | 16.5 MB (distroless, non-root) |
| Run all checks | `.\backend\gateway\scripts\check.ps1` (`-Fix` to autofix) |

**Both**

| Check | Result |
| --- | --- |
| CI | GitHub Actions on every push and pull request (`.github/workflows/ci.yml`): control plane, gateway (with `-race` and an image build), mock backend |
| API contract | `backend/control-plane/openapi.json`, checked by a test |

## Step 0: Go basics (Ali, in parallel)

| # | Step | Status |
| --- | --- | --- |
| 0.1 | Install Go and finish [A Tour of Go](https://go.dev/tour/) | 🔄 Go 1.27.4 installed |
| 0.2 | Small `net/http` server that returns JSON | ⬜ |
| 0.3 | One logging middleware | ⬜ |
| 0.4 | `httputil.NewSingleHostReverseProxy` against the mock backend | ⬜ |

## M1: Skeleton ✅

| # | Step | Status | Commits |
| --- | --- | --- | --- |
| 1.1 | Repo, folder layout, git, `.env.example`, README, CLAUDE.md | ✅ | `bcfe2e9`, `2dd7fc3` |
| 1.2 | Docker Compose: Postgres, Redis, mock backend | ✅ | `bcfe2e9`, `38468d9` |
| 1.3 | FastAPI base: settings, async SQLAlchemy, Alembic, `/health` | ✅ | `f26199d` |
| 1.4 | `workspaces` + `admins`, seeding, login, JWT, `get_current_admin` | ✅ | `ae569be`, `20389ac`, `a2b50ee` |
| 1.5 | `services` table + CRUD, shared errors and pagination | ✅ | `8266324`, `ec91f15` |
| 1.6 | Test review: ruff, mypy strict, coverage, gap tests | ✅ | `469f7a9`, `de5cba9` |

**Outcome:** an admin logs in and registers backend services through the API. The whole stack starts with `docker compose up`.

## M2: Control plane done ✅

| # | Step | Status |
| --- | --- | --- |
| 2.1 | `routes`, `consumers` and `api_keys` models and migrations | ✅ `ee9cf45`, `bfabeae` |
| 2.2 | CRUD endpoints for routes and consumers | ✅ `a85435d`, `9911ea2`, `0cde4fe` |
| 2.3 | API key generation (shown once, stored hashed) and revocation | ✅ `c89c9c6`, `26d9258` |
| 2.4 | `config_version` bump on every change (Postgres triggers + NOTIFY) | ✅ `57daa61`, `a144414` |
| 2.5 | `/internal/config` with the shared-secret check, ETag/304 and long polling | ✅ `52dbcb3`, `c9d6517`, `c9edaa3` |
| 2.6 | Tests for every endpoint: guard tests, end-to-end flow, OpenAPI contract, faster suite, CI | ✅ `1b97eef`, `8d43b87`, `70f5644`, *this commit* |

**Outcome:** the control plane is complete, and the gateway can read one config snapshot (instantly, via long polling).

## M3: Gateway proxies (Go)

| # | Step | Status |
| --- | --- | --- |
| 3.1 | Go module, `main.go`, graceful shutdown, `/health` | ✅ `430d193`, `3173814`, *this commit* |
| 3.2 | Config long-poll loop and atomic snapshot store | 👉 Next |
| 3.3 | Route matching by longest prefix and method | ⬜ |
| 3.4 | Reverse proxy with per-route timeout, 502 and 504 | ⬜ |
| 3.5 | Request ID, recover and logger middleware | ⬜ |
| 3.6 | API key auth middleware with 401 and 403 | ⬜ |
| 3.7 | Unit tests for matching and auth | ⬜ |

**Outcome:** requests flow client → gateway → mock backend, with key auth.

## M4: MVP done

| # | Step | Status |
| --- | --- | --- |
| 4.1 | Token-bucket Lua script and its Go wrapper | ⬜ |
| 4.2 | Rate-limit middleware with headers and 429 | ⬜ |
| 4.3 | End-to-end test script across the whole stack | ⬜ |
| 4.4 | k6 load test and the p95 latency the gateway adds | ⬜ |
| 4.5 | Final README with setup steps and an architecture picture | ⬜ |

**Outcome:** the MVP definition of done from PLAN.md section 1.

## MT: Multi-tenancy (after the MVP)

| # | Step | Status |
| --- | --- | --- |
| MT.1 | Workspaces API: create and list | ⬜ |
| MT.2 | Admin roles per workspace and inviting admins | ⬜ |
| MT.3 | Route `path_prefix` unique per workspace | ⬜ |
| MT.4 | Gateway finds the tenant (e.g. subdomain) | ⬜ |
| MT.5 | `/internal/config` grouped by workspace | ⬜ |
| MT.6 | Isolation tests across workspaces | ⬜ |

## v2a: Admin UI (after the MVP)

| # | Step | Status |
| --- | --- | --- |
| A.1 | Vite project, layout, auth guard, generated API types | ⬜ |
| A.2 | Pages: Login, Services, Routes, Consumers, Consumer detail | ⬜ |
| A.3 | CORS on the control plane for the dashboard origin | ⬜ |

Later phases (v2b plans and analytics, v3 portal and extras) are planned after these.

## Decisions log

Choices made along the way that differ from, or add to, the original plan.

| Date | Step | Decision | Why |
| --- | --- | --- | --- |
| Nov 15 | 1.1 | Repo root is the existing `API_Management_Platform` folder | It already existed and matched the GitHub repo |
| Nov 15 | 1.1 | Services grouped into `backend/` and `frontend/` | Easier to see which side each service is on |
| Nov 15 | 1.2 | Docker Postgres on host port **5434** | Local Postgres 18 and 14 already use 5432 and 5433 |
| Nov 16 | 1.3 | Apps run **only in Docker** (one DB address) | No confusion between container and host addresses |
| Nov 16 | 1.4 | **Option C: tenant-ready data model**, one `default` workspace | Cheap now, avoids a painful migration later |
| Nov 16 | 1.4 | argon2, PyJWT HS256 (60 min), JSON login, seed at startup, auto-migrate | Modern defaults, nothing to remember by hand |
| Nov 16 | 1.4 | Tests use a separate `apim_test` database | Tests never touch real data |
| Nov 16 | 1.5 | Layered code: router → crud → models, shared errors and pagination | Clean architecture, reused by every feature |
| Nov 16 | 1.6 | ruff + mypy strict + coverage ≥ 90%, run by `check.ps1` | The quality bar is enforced by tools, not memory |
| Nov 16 | 2.1 | Composite FKs on (`parent_id`, `workspace_id`) for routes and keys | The database itself blocks cross-tenant links |
| Nov 16 | 2.1 | Service with routes can't be deleted (409); deleting a consumer deletes its keys | No dangling routes; keys mean nothing without their owner |
| Nov 16 | 2.1 | `consumers.status` is text + CHECK, not a Postgres ENUM; consumer email not unique | Easy to extend; one developer can own several apps |
| Nov 16 | 2.1 | Route defaults: `auth_required=true`, `strip_prefix=false` | Secure by default |
| Nov 16 | 2.1 | **Rate limit per consumer** (`rl:{consumer_id}`), not per key | The limit is set on the consumer; per-key buckets would multiply it with every new key |
| Nov 16 | 2.1 | `.gitattributes`: LF everywhere, CRLF for `.ps1` | CRLF breaks scripts in Linux containers; same behavior on every clone |
| Nov 16 | 2.2 | Shared CRUD core: `paginate`, `SlugName`, `PartialUpdate`, `error_responses`, `commit_or_raise` | Write each rule once; every feature behaves the same |
| Nov 16 | 2.2 | Route `path_prefix` and `methods` normalized; a service outside the workspace is 422 | No near-duplicate routes; bad body references are input errors, not missing URLs |
| Nov 16 | 2.2 | List filters: routes `?service_id=`, consumers `?status=`; block = PATCH status | Needed by the dashboard pages; matches the plan's "update (including block)" |
| Nov 16 | 2.3 | Revoke keeps the row (`revoked_at`), idempotent, no un-revoke | History stays; a possibly leaked key is never trusted again |
| Nov 16 | 2.3 | Max 10 active keys per consumer, enforced with a row lock | Stops key sprawl, even under parallel requests |
| Nov 16 | 2.3 | `Cache-Control: no-store` on key creation; retry on hash collision | Secrets never cached; correct even in the impossible case |
| Nov 17 | 2.4 | **Long polling** (`?wait=30`) instead of polling every 5 s | Changes reach the gateway in milliseconds; 6× fewer idle requests; still plain HTTP |
| Nov 17 | 2.4 | `config_version` bumped by **Postgres triggers** that also send `NOTIFY config_changed` | No code path can forget it; cascades covered; a failed change bumps nothing |
| Nov 17 | 2.4 | `config_version` is a singleton (`id` = 1 with a CHECK), no uuid, no `workspace_id` | System state, not tenant config; one global version for the MVP snapshot |
| Nov 17 | 2.5 | Snapshot read in one `REPEATABLE READ` transaction | The version label always matches the data, so no change can be missed |
| Nov 17 | 2.5 | Blocked consumers' keys stay in the snapshot, marked | The gateway can answer 403 (blocked) instead of 401 (unknown key) |
| Nov 17 | 2.5 | One `LISTEN` connection per process, auto-reconnect; DB re-check after every wake-up; one snapshot build per version | Instant updates that stay correct even when a notification is lost; no thundering herd |
| Nov 17 | 2.5 | `uvicorn --timeout-graceful-shutdown 3` | Held long-poll requests don't delay restarts |
| Nov 17 | 2.6 | Guard tests discover every endpoint from the app | New endpoints are checked for auth and docs automatically |
| Nov 17 | 2.6 | Commit `openapi.json` and test it | API changes show up as diffs; the dashboard can generate its types from it |
| Nov 17 | 2.6 | Test cleanup with `DELETE` instead of `TRUNCATE` (measured: 86.8 s → 35.1 s) | Measured first: hashing wasn't the bottleneck, so it stays at full strength |
| Nov 17 | 2.6 | CI on GitHub Actions (control plane + mock backend), throwaway env values | Every push is checked on a clean machine; no secrets in the repo |
| Nov 18 | 3.1 | Gateway packages `internal/settings` (env vars) and `internal/server` (HTTP server), added to PLAN.md section 9 | `config` stays for the snapshot store; `main.go` stays thin wiring |
| Nov 18 | 3.1 | Server: `ReadHeaderTimeout` 5 s, `IdleTimeout` 60 s, no global read/write timeout; `SHUTDOWN_TIMEOUT` 5 s | Stops Slowloris without cutting slow valid responses (per-route timeouts come in 3.4); finishes before Docker's 10 s kill |
| Nov 18 | 3.1 | Image: multi-stage build into distroless/static, non-root; `gateway healthcheck` subcommand | 16.5 MB with no shell to attack; the binary checks itself because the image has no curl |
| Nov 18 | 3.1 | Gateway container gets only its own env vars, not `env_file: .env` | Least privilege: it never needs the DB password or JWT secret |
| Nov 18 | 3.1 | Go tests run locally on Windows; `-race` runs in CI | No database, no deps, so local equals Docker; the race detector needs cgo |
