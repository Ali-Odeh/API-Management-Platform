# Roadmap and progress

Where the project stands, step by step. The *what* and *why* of each milestone live in [PLAN.md](PLAN.md) section 10. This file tracks *progress*, and it is updated at the end of every step.

**Last updated:** Nov 21, 2026 · **Current step:** MT.1 done (GoTrue login, `users`, Platform Admin with MFA); next: MT.2

## Overview

| Milestone | Progress | Status |
| --- | --- | --- |
| Step 0: Go basics | ⬜⬜⬜⬜ 0/4 | 🔄 Ali, in parallel (A Tour of Go) |
| M1: Skeleton | ✅✅✅✅✅✅ 6/6 | ✅ Done |
| M2: Control plane done | ✅✅✅✅✅✅ 6/6 | ✅ Done |
| M3: Gateway proxies (Go) | ✅✅✅✅✅✅✅ 7/7 | ✅ Done |
| M4: MVP done | ✅✅✅✅✅ 5/5 | ✅ Done |
| MT: Multi-tenancy and login | ✅⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 1/12 | 🔄 In progress |
| v2: Plans and analytics (backend) | not planned yet | ⏳ After MT |
| v3: Portal and extras (backend) | not planned yet | ⏳ After v2 |
| FE: Frontend | ⬜⬜⬜⬜ 0/4 | ⏳ **Last**, after every backend milestone |

**MVP:** 24 of 24 steps done (100%). 🎉 The MVP is complete: routing, API keys, rate limiting, logging and live config, tested end to end and under load.

## Quality today

**Control plane (Python)**

| Check | Result |
| --- | --- |
| Tests | 480 passing (about 70–125 s), including a populated MVP-to-MT.1 upgrade; no GoTrue process needed (tokens signed with the test secret, a fake GoTrue admin API) |
| Coverage | 100% (minimum 90%) |
| ruff lint and format | clean |
| mypy strict | clean |
| Run all checks | `.\backend\control-plane\scripts\check.ps1` (`-Fix` to autofix) |

**Gateway (Go)**

| Check | Result |
| --- | --- |
| Tests | 362 passing (102 tests plus 260 table cases and fuzz seeds), about 3 s; the Redis tests need Docker's Redis; mock backend 7 |
| Coverage | **98.1%** total (minimum 90%, enforced): 6 packages at 100%, config 99.0%, server 97.4%, ratelimit 95.2%, main 87.0% |
| Fuzz tests | 5 (paths, matching, prefix stripping, request IDs); about 1.6 million random inputs in the first runs; 10 s each in CI |
| Benchmarks | match **0.04 / 0.4 / 4–5 µs** for 10 / 100 / 1000 routes (no allocations); key check **0.28 µs** for 100 or 100,000 keys; whole chain without network **≈ 8.5 µs**; one rate-limit check **≈ 0.48 ms** (Windows → Docker's Redis, so a round trip dominates) |
| Auth (live) | no key 401, wrong key 401, valid 200, blocked 403, unblocked 200, revoked 401; key never in logs or at the backend |
| Rate limit (live) | limit 5/min: 5 × 200 (remaining 4 → 0), then 429 with `Retry-After: 12`; Redis stopped → requests let through, one throttled warning; limiting resumes ~3 s after Redis is back |
| Gateway overhead (live) | `gateway_ms` **0.06–0.19 ms** per request (Docker, single requests) |
| gofmt, go vet | clean |
| Race detector | clean, including 1,000 requests while the config changes every ms (`go test -race` in a Linux container; also in CI) |
| Config propagation | admin change → gateway updated in **68 ms** (measured in Docker) |
| Proxy errors (live) | slow backend → **504 after 5.05 s** (route timeout 5 s); backend down → **502** |
| Image | 16.9 MB (distroless, non-root) |
| Run all checks | `.\backend\gateway\scripts\check.ps1` (`-Fix` to autofix) |

**Both**

| Check | Result |
| --- | --- |
| CI | GitHub Actions on every push and pull request (`.github/workflows/ci.yml`): control plane, gateway (`-race`, 90% coverage gate, fuzz runs, image build), mock backend, then the end-to-end job on a fresh stack with a k6 smoke run |
| API contract | `backend/control-plane/openapi.json`, checked by a test |
| End-to-end tests | **21 stories pass in about 6 s** (`docker compose run --rm e2e`), including GoTrue login with password and TOTP; nothing left behind (the e2e MFA factor is removed, the test person reused); fresh stack healthy in 17 s (measured at step 4.3, before GoTrue) |
| Load test (k6) | 300 requests/s, 3 × 30 s, **0 errors**: the gateway adds **0.77 ms median / 0.95 ms p95** (own code ≈ 0.08 ms, Redis ≈ 0.3 ms, rest = extra network hop); `docker compose run --rm loadtest` |

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
| 3.2 | Config long-poll loop and atomic snapshot store | ✅ `e805370`, `9f9547c`, *this commit* |
| 3.3 | Route matching by longest prefix and method | ✅ `7d56624`, `3adc271`, *this commit* |
| 3.4 | Reverse proxy with per-route timeout, 502 and 504 | ✅ `8b22acb`, `b47bea2`, *this commit* |
| 3.5 | Request ID, recover and logger middleware | ✅ `1348149`, `ded8f8a`, *this commit* |
| 3.6 | API key auth middleware with 401 and 403 | ✅ `562d980`, `8fe67eb`, *this commit* |
| 3.7 | Test review: whole gateway start to stop, load during config changes, fuzz tests, benchmarks, 90% coverage gate | ✅ `6347d95`, `8e80c76`, *this commit* |

**Outcome:** requests flow client → gateway → mock backend, with key auth, request IDs and one log line each. Tested start to stop, under load, with fuzzing; 98.6% coverage.

## M4: MVP done

| # | Step | Status |
| --- | --- | --- |
| 4.1 | Token-bucket Lua script and its Go wrapper | ✅ `792c9aa`, `34d5446`, *this commit* |
| 4.2 | Rate-limit middleware with headers and 429 | ✅ `fca379a`, `31c3dd5`, *this commit* |
| 4.3 | End-to-end test script across the whole stack | ✅ `970742a`, `cc12051`, *this commit* |
| 4.4 | k6 load test and the p95 latency the gateway adds | ✅ `3452329`, `8a21d3a`, *this commit* |
| 4.5 | Final README with setup steps and an architecture picture | ✅ `ede180f`, `4a7e125`, *this commit* |

**Outcome:** the MVP definition of done from PLAN.md section 1, proven by the end-to-end tests on every push and measured by the load test.

## MT: Multi-tenancy and login

**What and how:** PLAN.md (sections 3–11). **Why:** MT_ROLES_DECISIONS.md (decision numbers in brackets).

**How these steps are worked:** each step is one self-contained piece of work. The agent plans its own sub-tasks, finishes the **whole** step (code, migrations, tests, docs) and hands back only when every "done when" item holds and both `check.ps1` scripts and the e2e tests are green. Work is committed as logical commits along the way, not one big commit. Rules in `AGENTS.md`.

| # | Step | Status |
| --- | --- | --- |
| MT.1 | Identity: GoTrue, `users`, JWT verification, Platform Admin with MFA | ✅ |
| MT.2 | Workspaces and roles, URLs under `/workspaces/{slug}`, isolation tests | ⬜ |
| MT.3 | Audit log | ⬜ |
| MT.4 | Members and owners | ⬜ |
| MT.5 | Workspace lifecycle and quotas | ⬜ |
| MT.6 | Incremental config protocol | ⬜ |
| MT.7 | Gateway tenancy | ⬜ |
| MT.8 | Workspace limits in the gateway | ⬜ |
| MT.9 | SSRF protection | ⬜ |
| MT.10 | Load balancing and health checks | ⬜ |
| MT.11 | CORS and body size limit | ⬜ |
| MT.12 | MT end to end, load test, docs | ⬜ |

The order follows dependencies: identity before roles, roles before audit and members, the config protocol before the gateway's tenancy, targets after SSRF (both live in the gateway's transport).

### MT.1 Identity [4, 12, 13]

**Outcome:** people log in with GoTrue; the control plane trusts GoTrue's token and keeps its own `users`. The old login is gone.

- Compose: `auth` (GoTrue, pinned version, own `auth` database on the same Postgres) and `mailpit`; `scripts/init-env` generates the new secrets
- GoTrue settings from PLAN.md section 9: no public sign-up, 10-minute tokens, 12-character passwords, SMTP to Mailpit, tightened sign-in rate limits
- `users` table (Alembic) replaces `admins`: the migration moves the existing admin, makes them `is_platform_admin` and drops the argon2 hash
- JWT verification dependency (signature, expiry, audience); just-in-time `users` row on the first request; `is_active = false` → `403 user_disabled`
- First Platform Admin created in GoTrue at startup from `PLATFORM_ADMIN_EMAIL` if missing
- Platform endpoints require `aal2` (MFA) → `403 mfa_required`
- `PATCH /api/v1/users/{id}` (Platform Admin): disable or enable, also banning or unbanning in GoTrue
- Remove the login endpoint, argon2, token issuing and the MVP auth settings; `GET /api/v1/me`
- Test helpers sign tokens with the test GoTrue secret, so unit tests don't need a GoTrue process
- `set_updated_at` trigger on every table (today `updated_at` is set only by the ORM)

**Done when:** the Platform Admin logs in through GoTrue (password, then TOTP) and calls the admin API with GoTrue's token; a disabled user is refused on the next request and can't refresh; all existing tests pass with GoTrue tokens; e2e logs in through GoTrue.

**Status:** ✅ Done (Nov 21). Commits `04f3aab` (`updated_at` trigger), `b1a984f` (GoTrue and Mailpit in the stack), `17bccf1` (GoTrue login, `users` replace `admins`, disabling users), then the docs update (`a567a15`). Review fixes: `85fddbb` (users bound to their GoTrue account; locked, failure-safe disable/enable), `42f8427` (Compose starts without a Platform Admin password; Mailpit on 127.0.0.1), `7a7d885` (populated MVP upgrade test, `.env` upgrade guide), the docs update `384b1f4`; second review: `c2c144d` (a bound admin is never rebound automatically; `scripts/relink_user`), `f8cb671` (a failed GoTrue lookup keeps a disable), then the docs update. Every "done when" item is covered by e2e stories (`e2e/test_identity.py`) and unit tests. GoTrue `v2.197.0`, Mailpit `v1.33.0`. Choices made while building are in the decisions log (MT.1 rows).

### MT.2 Workspaces and roles [0, 2, 5, 13]

**Outcome:** every admin endpoint lives under a workspace, and every call is checked against the caller's role in that workspace.

- `workspace_members` (`owner`, `api_admin`, `viewer`); the MT.1 admin becomes Owner of `default`
- Permission map in code and `require_permission(...)`; one query resolves slug + membership; 404 for unknown or not-a-member, 403 for too low a role; Platform Admin passes for every workspace
- Move every endpoint to `/api/v1/workspaces/{slug}/...`; remove the unscoped URLs; `GET /api/v1/me/workspaces`
- `GET, POST /api/v1/workspaces` (Platform Admin, MFA): create `{name, slug, owner_email}` (owner must already exist until MT.4 adds invitations), list
- Route `path_prefix` unique per workspace instead of globally
- Every `crud.py` function takes `workspace_id` first
- **Permission guard test** and **isolation test**, both discovering every endpoint from the app
- `openapi.json` regenerated and committed; e2e moved to the new URLs

**Done when:** a Viewer can read but gets 403 on every write; a member of workspace A gets 404 on every endpoint of workspace B and 422 when referencing B's objects; the two guard tests cover every endpoint and fail if a new one is added without a permission.

### MT.3 Audit log [11]

**Outcome:** every admin change leaves a record of who, what, when and before/after.

- `audit_events` table; the application's database user gets only `INSERT` and `SELECT` on it
- `audit.record(...)` written in the same transaction as each change; `changes` holds changed fields only; no secrets (keys by prefix only)
- Every existing write endpoint records its event (`service.create`, `route.update`, `key.revoke`, ...)
- **Audit guard test:** every mutating endpoint writes exactly one event; a failed change writes none
- `GET /api/v1/workspaces/{slug}/audit-events` (`audit:read`) and `GET /api/v1/audit-events` (Platform Admin), with filters and pagination
- Retention job (default 365 days) running as a separate database user

**Done when:** the guard test covers every mutating endpoint; a rolled-back change leaves no event; Platform Admin actions in a workspace appear in that workspace's log with `actor_type = platform_admin`.

### MT.4 Members and owners [8, 9]

**Outcome:** Owners build their team; a workspace can never lose its last owner.

- `POST /members {email, role}`: existing user → member at once; new person → GoTrue invite email (Mailpit) → member at first login; existing member → 409
- `PATCH` (change role) and `DELETE` (remove) by Owners; `DELETE /api/v1/me/workspaces/{slug}` (leave)
- Owner rules: several owners; nobody changes their own role; the last active human owner can't be demoted, removed or leave (`409 last_owner`); owner rows locked with `SELECT ... FOR UPDATE`
- Platform Admin can assign an owner in any workspace (audited)
- `POST /api/v1/workspaces` now accepts a new `owner_email` (invites them)
- Audit: `member.add`, `member.role_change`, `member.remove`, `member.leave`, `member.owner_assigned_by_platform`

**Done when:** an invitation email arrives in Mailpit and leads to a working membership; a concurrency test with two owners demoting each other at the same moment always leaves one owner; all rules above have tests.

### MT.5 Workspace lifecycle and quotas [6, 10]

**Outcome:** workspaces can be suspended, deleted, restored and purged; each has adjustable limits.

- `workspaces`: `status`, `status_reason`, `deleted_at`, `purge_after`, limit columns (NULL = platform default)
- Suspend / unsuspend (Platform Admin): writes → `403 workspace_suspended`, reads still work
- Delete `{confirm: "<slug>"}` (Owner or Platform Admin) → hidden from members; restore within 30 days (Platform Admin)
- Daily purge job deletes all workspace data after 30 days, keeps the row with status `purged`; slugs are never reused; `default` can't be suspended or deleted
- Quotas (services, routes, consumers) → `409 quota_exceeded`; `PATCH /limits` (Platform Admin)
- Lifecycle audit events as platform-level events (`workspace_id` NULL)

**Done when:** every transition and its refusals are tested; a purged workspace's slug can't be taken again; quotas are enforced under parallel creation (no overshoot).

### MT.6 Incremental config protocol [7]

**Outcome:** a change in one workspace sends only that workspace to the gateways.

- Control plane: `config_seq` replaces `config_version`; `workspace_config (version, seq)`; statement-level triggers with transition tables bump the touched workspaces (now also on `workspaces` changes). Follow the Postgres rules in PLAN.md section 5: three triggers per table (no `OR` with transition tables) and config-column filtering inside the function (no `UPDATE OF` with transition tables); an update that changes only `updated_at` or `last_used_at` bumps nothing (test)
- `/internal/config?since=&wait=` with `{seq, full, workspaces, deleted}` exactly as in PLAN.md section 6; effective limits in the answer; per `(workspace, version)` cache; `?workspaces=` filter
- Gateway: snapshot becomes `ByHost` + `Workspaces`; merge that reuses unchanged workspaces; `full` handling and the protocol-error fallback to `since=0`
- Until MT.7 the gateway still routes every request to `default`, so all e2e stories keep working

**Done when:** a change in workspace A produces an answer containing only A (test); gateway start, database reset and deleted workspaces are covered by tests; the existing race test (requests during constant config changes) passes with `-race`; config propagation is still measured in milliseconds.

### MT.7 Gateway tenancy [1, 10, 17]

**Outcome:** the gateway serves each workspace on its own subdomain and never mixes them.

- `tenant` middleware: Host → workspace (`GATEWAY_BASE_DOMAIN`, port dropped, bare domain → `default`); `404 workspace_not_found`; `403 workspace_suspended`
- Route matching and key lookup inside the workspace only; a key of another workspace → `401 invalid_api_key`
- Strip client `X-Workspace-ID`, set the trusted one
- `workspace_id` on every gateway log line and every control-plane log line
- Fuzz test for host parsing

**Done when:** e2e shows two workspaces with the same `/v1/orders` reaching different backends via `acme.localhost` and `globex.localhost`; a key used on the other workspace's host gets 401; a suspended workspace gets 403 within one config update.

### MT.8 Workspace limits in the gateway [6]

**Outcome:** one workspace can't take the gateway from the others.

- Lua script with the workspace bucket and the consumer bucket in one call (spend only if both allow); public routes use the workspace bucket; log `limited_workspace`
- In-flight semaphore per workspace per instance → `503 workspace_overloaded` with `Retry-After: 1`
- Fail-open behavior unchanged when Redis is down

**Done when:** Redis tests show both buckets, refusal by either, and atomicity under 100 parallel requests; a slow backend in workspace A can't use more than its in-flight share while workspace B keeps answering (test); e2e covers the workspace rate limit.

### MT.9 SSRF protection [3]

**Outcome:** no tenant can make the gateway reach internal addresses.

- `egress` package: IP policy (default deny list from PLAN.md section 7, `UPSTREAM_ALLOW` exceptions), guarded `net.Dialer` with the `Control` hook; IPv4-mapped IPv6 handled
- `502 upstream_not_allowed` and a warning log with workspace and target
- Control plane: scheme check and early 422 for internal IP literals and known internal names on save
- Compose and CI set `UPSTREAM_ALLOW=mock-backend`
- Fuzz test for the policy; a DNS-rebinding test (a name resolving to an internal IP at connect time is refused)

**Done when:** e2e shows a service pointing at `http://redis:6379` refused with `upstream_not_allowed`; all other e2e stories pass with the allow list.

### MT.10 Load balancing and health checks [14]

**Outcome:** a service runs on several backend instances; traffic avoids broken ones.

- `service_targets` table; migration turns each `upstream_url` into one target; target endpoints; health-check settings on services
- Gateway `upstream` package: smooth weighted round robin, active checks (interval, thresholds), passive ejection, `503 no_healthy_upstream`, `target_id` in the log
- Balancer and health state survive config updates when a service's targets didn't change; checkers stop when targets leave
- Health checks use the SSRF dialer
- Compose gets a second mock-backend instance for tests

**Done when:** unit tests show the weighted distribution over many picks, skipping, recovery and passive ejection; e2e shows traffic spread over two targets and moving off a stopped one within the configured thresholds.

### MT.11 CORS and body size limit [15, 16]

**Outcome:** browser apps can call APIs through the gateway; oversized bodies never reach a backend.

- Route CORS settings; preflight answered by the gateway before auth, not forwarded, not rate-limited; headers on normal answers (`Vary: Origin`, no `*` with credentials)
- Route `max_body_bytes` with platform default and ceiling (control-plane settings; effective value in the snapshot); `413 request_too_large` from `Content-Length`; `MaxBytesReader` for bodies without a length

**Done when:** e2e covers a preflight from an allowed and a refused origin, a credentialed call, and a 413 both with and without `Content-Length`.

### MT.12 MT end to end [all]

**Outcome:** the MT definition of done (PLAN.md section 1) is proven on every push.

- e2e stories for everything in PLAN.md section 11 "MT adds"; invitation emails read through Mailpit's API
- k6 load test updated for host-based routing and two targets; numbers recorded here
- README, `docs/DEVELOPMENT.md` and this file updated (quality tables, commands, new services)
- Decisions taken during MT added to the decisions log below

**Done when:** CI is green on a fresh stack, including the MT e2e stories and the k6 smoke run; the gateway overhead measured again and written down.

## v2 and v3: backend (after MT)

Plans and analytics (v2), then the developer portal API and extras (v3). Planned in detail, step by step, once MT is done. Backend only: their pages are built in FE.

## FE: Frontend (last)

**The whole frontend is built last, all at once**, after every backend milestone, by Claude, against the final control-plane API and its committed `openapi.json`. Pages and areas are listed in PLAN.md section 8. The app is called by its three areas: **Dashboard** (workspace members, by role), **Platform** (Platform Admin) and **Developer portal**; not "Admin UI", because most of its users are not admins.

| # | Step | Status |
| --- | --- | --- |
| FE.1 | Foundation: Vite, React, TypeScript, layout, GoTrue login and MFA, account page, workspace switcher, role-aware routing and buttons, generated API types (Vite proxies `/api`, so no CORS is needed) | ⬜ |
| FE.2 | Dashboard: Overview, Services and service detail (targets, health), Routes (CORS, body limit), Consumers and keys, Members, Activity, Settings, Plans, Logs | ⬜ |
| FE.3 | Platform: Workspaces (create, suspend, restore, limits), Users (disable, enable), platform activity | ⬜ |
| FE.4 | Developer portal: sign-up and login, API catalog, API docs with try-it, own keys, own usage | ⬜ |

## Decisions log

Choices made along the way that differ from, or add to, the original plan. The MT design decisions (Nov 20, 2026) are recorded in [MT_ROLES_DECISIONS.md](MT_ROLES_DECISIONS.md); choices made while building MT are added here as before.

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
| Nov 18 | 3.1 | Route `path_prefix` `/health` (and below) is reserved: 422 | The gateway answers it itself, so such a route would be created but never reached |
| Nov 18 | 3.1 | Gateway on host port **8081** (8080 inside Docker) | Another Windows app holds 127.0.0.1:8080, so tools using that address hit the wrong server |
| Nov 18 | 3.2 | Gateway starts without waiting for config; no `depends_on` on the control plane | A slow or down control plane never keeps the gateway from starting; 3.3 answers 503 until the first config |
| Nov 18 | 3.2 | Backoff 1 s → 30 s with jitter; at most one poll per 250 ms | Gateways don't retry in lockstep; a misbehaving control plane can't cause a busy loop |
| Nov 18 | 3.2 | Broken snapshot rejected whole; a single bad route or key skipped with a warning | One bad row must not block other changes, such as key revocations |
| Nov 18 | 3.2 | A lower config version is accepted | The control plane is the source of truth (e.g. after a database reset) |
| Nov 18 | 3.2 | `Secret` type for `INTERNAL_TOKEN`; settings errors reported all at once | The token can't leak into logs or errors; one restart fixes every bad value |
| Nov 18 | 3.3 | **405** `method_not_allowed` with `Allow` when a path fits but no route allows the method (plan said 404) | The correct HTTP answer; tells the client exactly what is wrong |
| Nov 18 | 3.3 | Longest prefix **among routes that allow the method**; exact method match (no implied HEAD) | Falls back to a shorter route like Kong; simple and predictable |
| Nov 18 | 3.3 | Snapshot read once per request and passed on in the context | Matching and the key check always use the same config version |
| Nov 18 | 3.3 | Encoded traversal (`%2e%2e`) rejected with **400** `invalid_path` | Go's mux redirects `../` but passes the encoded form through; found by a test |
| Nov 18 | 3.3 | Plain scan of the sorted route list; `respond` package for JSON errors | Fast enough for hundreds of routes and easy to swap; one error shape everywhere |
| Nov 18 | 3.3 | Temporary 501 `not_implemented` after a match | Matching can be tried live; 3.4 replaces it with the proxy |
| Nov 19 | 3.4 | One shared `ReverseProxy` + one pool; target picked per request with `Rewrite` | Config changes apply to the next request; `Rewrite` (not `Director`) drops client-sent `X-Forwarded-*` |
| Nov 19 | 3.4 | Pool: 100 idle per backend, 1000 total, 90 s idle; 5 s connect and TLS timeouts | Go's default of 2 idle per backend would reopen connections under load |
| Nov 19 | 3.4 | Route timeout covers the whole answer (Envoy style); connect timeout also 504 | Simple and matches `timeout_ms`; WebSockets would need a different timeout later |
| Nov 19 | 3.4 | Encoded paths kept exactly (`RawPath`), also after `strip_prefix` | `a%2Fb` and `a/b` are different resources |
| Nov 19 | 3.4 | Client hang-up recorded as **499** (nginx convention), not logged as a backend failure | Not the backend's fault; the 3.5 log line still shows it |
| Nov 19 | 3.4 | No retries, body limit or WebSockets yet | Retries need several backend instances (v3); the rest when a route needs them |
| Nov 19 | 3.4 | Mock backend `?delay_ms=` (0–30000) | Shows a 504 live without a real slow service |
| Nov 19 | 3.5 | Order **request ID → logger → recover** → router (plan had recover outermost) | The logger then records a panic's 500; chi's documented order |
| Nov 19 | 3.5 | Keep a safe client `X-Request-ID` (Kong style), else a new 128-bit ID | Customers can report an ID and it is found; the charset check stops log injection |
| Nov 19 | 3.5 | Log adds `gateway_ms`, `client_ip`, `bytes`; times in ms with µs precision; no query string | `gateway_ms` shows the gateway's own cost at a glance (Kong's "kong latency") |
| Nov 19 | 3.5 | `upstream_ms` = time to the backend's response headers | `latency − upstream` is then exactly the gateway's share |
| Nov 19 | 3.5 | `/health` not logged; middleware wraps API traffic only | Docker's checks would add ~17,000 useless lines a day |
| Nov 19 | 3.5 | `reqctx` package: a per-request "notes card" filled by router, forwarder, auth | The logger sees inner results without packages importing each other |
| Nov 19 | 3.6 | Two 401 codes: `missing_api_key` and `invalid_api_key` (plan had one) | Client developers see at once whether they forgot the key or sent a wrong one; revoked and unknown stay identical |
| Nov 19 | 3.6 | `X-API-Key` header only; removed before forwarding; gateway sets `X-Consumer-ID` and strips a client-sent one | Keys in URLs leak; the backend never needs the secret; nobody can pose as another consumer |
| Nov 19 | 3.6 | Auth after route matching (404 before 401), same snapshot as the router | Kong's order; one consistent config version per request |
| Nov 19 | 3.6 | `WWW-Authenticate: ApiKey header="X-API-Key"` on 401; `key_id` in the log | HTTP requires the challenge on 401; `key_id` shows which key to revoke |
| Nov 19 | 3.6 | Gateway hash checked against a value computed by the control plane's Python code | Both sides must hash identically or no key would ever match |
| Nov 19 | 3.7 | `serve(ctx, ...)` takes its context; `run()` owns the signals | A test can start and stop the real program; behavior unchanged |
| Nov 19 | 3.7 | Fuzz tests against independent reference rules; 10 s each in CI | Security-relevant path checks are tried with inputs nobody thought of; found the `//` bug on day one |
| Nov 19 | 3.7 | `//` is not a clean path (fix) | The trailing-slash rule also matched the root, so `/%2F` passed the check |
| Nov 19 | 3.7 | Benchmarks run by hand, numbers in ROADMAP | CI timing is too noisy; the numbers back the linear-scan decision of 3.3 |
| Nov 19 | 3.7 | Gateway coverage gate 90% in `check.ps1` and CI | Same bar as the control plane; enforced by tools, not memory |
| Nov 19 | 4.1 | Lua script reads **Redis's clock** (`TIME`), not a time from the gateway (plan draft passed `now_ms`) | Gateways with different clocks can't make time seem to run backwards in a bucket |
| Nov 19 | 4.1 | Script also returns `retry_after_ms` | An exact `Retry-After` for 429 in 4.2 |
| Nov 19 | 4.1 | go-redis v9 (first external dependency), script embedded with `go:embed`, 50 ms timeout | Official client with EVALSHA/EVAL fallback; real Lua file; a slow Redis must not slow requests |
| Nov 19 | 4.1 | Rate-limit tests against a real Redis (DB 15 locally, a service in CI), skipped without `REDIS_TEST_URL` | Atomicity is the point, and only a real Redis proves it |
| Nov 19 | 4.2 | Rate limit after auth; public routes not limited in the MVP | Needs the consumer; 401/403 cost no budget; per-IP limits for public routes are a v2 idea |
| Nov 19 | 4.2 | Fail-open warning throttled to one per 10 s with a count | An outage at 1,000 req/s would otherwise write 1,000 warnings per second |
| Nov 19 | 4.2 | `Retry-After` in whole seconds, rounded up, at least 1; `X-RateLimit-*` headers | Retrying early would only be refused; the X- names are what GitHub and Kong send |
| Nov 19 | 4.2 | `ratelimit` field in the request log (`ok` / `limited` / `unavailable`) | Shows exactly when requests were let through because Redis was down |
| Nov 19 | 4.2 | `REDIS_URL` required and a Secret; lazy client, 50 ms timeouts, no retries | It may hold a password; Redis down at startup is fine; a slow Redis must not slow requests |
| Nov 19 | 4.2 | go-redis "maintenance notifications" turned off | They resolved the hostname while building the client: 2–4 s late start whenever Redis was down |
| Nov 19 | 4.2 | Redis tests: no `FLUSHDB`, own consumer IDs; the real-program test retries a round that failed open | Packages run in parallel on one test database; a slow Redis rightly fails open, which proves nothing |
| Nov 19 | 4.3 | End-to-end tests in pytest + httpx, in a new top-level `e2e/` folder | Readable stories in a language the team knows; separate from each service's own tests |
| Nov 19 | 4.3 | Runner in its own container, Compose profile `test` (`docker compose run --rm e2e`) | Apps run only in Docker; same internal addresses on Windows and in CI; never started by `up` |
| Nov 19 | 4.3 | Tests create uniquely named objects and always delete them; wait by polling, no fixed sleeps | Never touch real data; fast and not flaky (config arrives in ~70 ms) |
| Nov 19 | 4.3 | CI e2e job on a fresh stack after the unit jobs, throwaway `.env` | Every push proves the whole platform works, not just each part |
| Nov 19 | 4.4 | Three runs at one fixed rate: direct, gateway public route, gateway protected route | Differences isolate the gateway's own cost and the Redis cost; a fixed rate keeps the comparison fair |
| Nov 19 | 4.4 | 300 requests/s, after measuring the mock backend (p95 under 2 ms even at 1,000/s) | Well below the backend's limit, so backend queueing isn't counted as gateway time |
| Nov 19 | 4.4 | Consumer limit 100,000/min (the control plane's maximum) | Every request does the full Redis check, but none gets a 429 |
| Nov 19 | 4.4 | k6 in Docker (Compose profile `test`); `gateway_ms.py` reads the gateway's own times from its log | Nothing to install; an inside view to check k6's outside view |
| Nov 19 | 4.4 | CI: 5 s smoke run at 20 requests/s, no timing claims | Shared CI machines are too noisy to measure; the smoke run keeps the script from breaking |
| Nov 19 | 4.5 | README is the project's front page; developer commands move to `docs/DEVELOPMENT.md` | A reader sees what it is, the proof and a quick start in one screen; contributors get a full guide |
| Nov 19 | 4.5 | Diagrams in Mermaid inside the README | GitHub renders them; they are edited like code; checked to render in Mermaid 11 |
| Nov 19 | 4.5 | `scripts/init-env.ps1` / `.sh` create `.env` with random secrets, never overwrite it | The quick start needs no hand-filled secrets; checked from scratch on a clean copy |
| Nov 19 | 4.5 | Quick start for both PowerShell and bash | GitHub readers use every OS; the walkthrough stays PowerShell |
| Nov 21 | MT.1 | `PLATFORM_ADMIN_PASSWORD` (optional) added next to `PLATFORM_ADMIN_EMAIL`: with it the account is created ready to log in; without it GoTrue sends an invitation email | The quick start, CI and e2e need a Platform Admin that can log in without reading an email; production can still leave it empty |
| Nov 21 | MT.1 | GoTrue gets its own database login (`gotrue`, `GOTRUE_DB_PASSWORD`), made by a one-shot `auth-db-init` job; `CONNECT` on `auth` and `apim` revoked from `PUBLIC` | Least privilege: GoTrue can't open the platform's database; the job works on fresh and existing volumes (Postgres init scripts only run on a fresh one). GoTrue's migrations need a role named `postgres` and an existing `auth` schema, so the job creates both |
| Nov 21 | MT.1 | Until MT.2, the service, route, consumer and key endpoints act on `default` and are open to **Platform Admins only**, without MFA; any other signed-in person gets `403 platform_admin_required` | There are no memberships before MT.2; the migrated admin keeps working exactly as before. No MFA there because in MT.2 they become Owner of `default`, and Owners don't need it |
| Nov 21 | MT.1 | `users.auth_user_id` is nullable only until the account is resolved; a token whose `sub` is unknown gets a new row with no rights, or `403 account_conflict` if its email is already taken. **Never linked by email** (changed after review; the first build linked by email) | A token's email doesn't prove it is the same person: a different GoTrue account with that address must not inherit an admin's rights. Rows made before their first login are linked through the trusted admin API instead: the Platform Admin at startup (which also links the migrated MVP admin), anyone at disable or enable |
| Nov 21 | MT.1 | Token checks: HS256 signature, `exp`, `aud = authenticated`, a UUID `sub` and an email; `role` is not checked | GoTrue v2.197 leaves `role` empty (`GOTRUE_JWT_DEFAULT_GROUP_NAME` is deprecated); the audience alone keeps the service-role key (no `aud`) from passing as a person, which a guard test checks on every endpoint |
| Nov 21 | MT.1 | 403 details are short codes: `user_disabled`, `mfa_required`, and the new `platform_admin_required` | The dashboard can act on them (e.g. start the MFA step) without parsing sentences |
| Nov 21 | MT.1 | `PATCH /api/v1/users/{id}`: `409` when a Platform Admin disables themselves; the row is locked (`FOR UPDATE`) before it is read and across the GoTrue call; on a GoTrue failure or timeout `502`, keeping the safe side (a disable stays, an enable isn't made); an unlinked person's account is found by exact email so the ban still blocks refresh, and a failed lookup counts like a failed ban (changed after review; the first build rolled everything back, and before the second review a failed lookup still did) | Parallel changes run in turn, so GoTrue ends like the row. A timeout can come after GoTrue acted, so "nothing changed" couldn't be promised; the safe side closes the API at once, and a retry (the call is idempotent) brings both in step |
| Nov 21 | MT.1 | GoTrue rate limits: `/token` 30 calls per 5 minutes per IP (default 150), MFA challenge and verify 10 per minute (default 15) | "Tightened sign-in rate limits" (PLAN.md section 9) with numbers that still leave room for e2e and k6 runs |
| Nov 21 | MT.1 | `set_updated_at` moves `updated_at` only when the row really changed (`ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*)`); the ORM now treats the column as database-owned (`server_onupdate`) | Writing the same values isn't a change; a guard test checks every table with `updated_at` has the trigger |
| Nov 21 | MT.1 | e2e: the Platform Admin's MFA factor is enrolled by the tests (named `e2e`) and removed at the end; the test person `e2e-person@example.test` is reused by every run; e2e gets `GOTRUE_SERVICE_ROLE_KEY` only to set up and clean up these accounts | GoTrue lets a person hold several factors, so one the developer enrolled is untouched; there is no endpoint to delete a user, so a fixed address keeps runs from piling up rows. TOTP codes are computed in 6 lines (RFC 6238) instead of a new dependency |
| Nov 21 | MT.1 | CI's e2e job builds `.env` with `scripts/init-env.sh` | The service-role key must be a JWT signed with that run's secret; the script makes one and gets tested on every push |
| Nov 21 | MT.1 | Dependencies: `argon2-cffi` removed; `httpx` (already a test dependency) is now a runtime one, for the GoTrue admin client | No password hashing left in the control plane; httpx is async and already used by the tests |
| Nov 21 | MT.1 | Startup links the Platform Admin only when the row has no GoTrue id yet (the migrated MVP admin, a new admin), to the account found through GoTrue's admin API (exact email over the paged substring `filter`), created or invited only if missing. A bound row is never rebound at startup: a different (or missing) account for that email is logged as an `ERROR` and nothing changes; `python -m scripts.relink_user <email>` rebinds on purpose (changed after the second review; startup used to rebind) | Restarts never resend a pending invitation. A recreated or foreign account with the configured email must not take over the admin's row by itself; an operator decides, and the log says how |
| Nov 21 | MT.1 | The e2e and load-test services pass `PLATFORM_ADMIN_*` and the service-role key with `:-`; the runners check them on start | Compose interpolates the whole file for every command, so `:?` there stopped `docker compose up` for an invited admin (empty password) |
| Nov 21 | MT.1 | Mailpit's UI is bound to 127.0.0.1 | Its inbox holds invitation and password-reset links |
| Nov 21 | MT.1 | Upgrading an MVP install: prepare the new `.env` **before** any Compose command (the new file needs the GoTrue secrets even for `down`), keeping the Postgres values and internal token from the old one and setting `PLATFORM_ADMIN_EMAIL` to the old `ADMIN_EMAIL`; keep the old file as `.env.mvp` until the stack is healthy and the admin's GoTrue login is checked (steps in DEVELOPMENT.md) | The volume keeps its original Postgres user and password; the migrated admin keeps its id and history and is linked at startup. A test upgrades a populated MVP database |
| Nov 21 | MT.1 | Startup grants Platform Admin only after resolving a first binding or verifying the existing GoTrue UUID. Explicit relinking requires a confirmed GoTrue ban on a disabled user's replacement account before saving the binding | A binding conflict cannot promote the previous identity. A disabled user stays blocked from refreshing tokens after recovery |
