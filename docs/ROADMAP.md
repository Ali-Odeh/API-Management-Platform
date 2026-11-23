# Roadmap and progress

Where the project stands, step by step. The *what* and *why* of each milestone live in [PLAN.md](PLAN.md) section 10. This file tracks *progress*, and it is updated at the end of every step.

**Last updated:** Nov 23, 2026 · **Current step:** FE.3 done; next: FE.4 (waiting for the owner's go)

## Overview

| Milestone | Progress | Status |
| --- | --- | --- |
| M1: Skeleton | ✅✅✅✅✅✅ 6/6 | ✅ Done |
| M2: Control plane done | ✅✅✅✅✅✅ 6/6 | ✅ Done |
| M3: Gateway proxies (Go) | ✅✅✅✅✅✅✅ 7/7 | ✅ Done |
| M4: MVP done | ✅✅✅✅✅ 5/5 | ✅ Done |
| MT: Multi-tenancy and login | ✅✅✅✅✅✅✅✅✅✅✅✅ 12/12 | ✅ Done |
| v2: Usage, plans and metrics (backend) | ✅✅✅ 3/3 | ✅ Done |
| Portal: developer portal (backend) | ✅✅ 2/2 | ✅ Done: **the backend is complete** |
| FE: Frontend | ✅✅✅⬜ 3/4 | 🔨 In progress |
| *Out of plan* | — | Kept for reference, not scheduled (see "Out of plan" below) |

**MVP:** 24 of 24 steps done (100%). 🎉 The MVP is complete: routing, API keys, rate limiting, logging and live config, tested end to end and under load.

**MT:** 12 of 12 steps done (100%). 🎉 Multi-tenancy and login are complete: the MT definition of done (PLAN.md section 1) is an e2e story run on every push.

**Backend:** complete (Nov 23, 2026). The 5 steps planned on Nov 22 (V2.1–V2.3, P.1–P.2) are done; the Portal definition of done (PLAN.md section 1) is an e2e story run on every push. Next: the frontend, all at once.

## Quality today

**Control plane (Python)**

| Check | Result |
| --- | --- |
| Tests | 2201 passing (about 16 min), including the users list with each person's workspaces and its filters (FE.3), developers' apps (made only for a published API on an offered plan of an active workspace, `pending` with a blocked consumer on a plan that needs approval, approved and rejected by members with the consumer following, a blocked developer's apps blocked in every workspace, 10 apps per developer, keys only for approved apps, usage and the month's quota from the request log), a portal guard over every portal endpoint (a blocked developer refused, another developer's apps and keys `404`, every portal write exactly one event by the developer in the workspace's log, none when it fails or only reads) (P.2), the two identities (a developer's token refused by every admin endpoint and a member's, the Platform Admin's with MFA included, by every portal endpoint), developers made just in time (parallel first requests make one, another account with a known email `403 account_conflict`, blocked `403 developer_blocked`), the API lifecycle and routes in at most one API, OpenAPI documents (shape, the 1 MiB limit to the byte, SQL NULL for none), the catalog (drafts, another workspace's APIs and suspended workspaces all 404) (P.1), plans (above the workspace's limit 422, one default that moves but is never unset or deleted, a plan in use kept by the foreign key too), the populated MVP database upgraded with every consumer's limit unchanged on a `Legacy <n>/min` plan, the per-IP limits reaching running gateways, the usage-ingest worker against a real Redis stream (each event stored once, a worker stopped mid-batch losing and doubling nothing, bad and out-of-window entries dropped, `last_used_at` forward only and at most once a minute without moving `updated_at` or the config), analytics numbers against known rows (buckets, percentiles, busiest routes and consumers, the request log's filters), the five logins' privileges, platform defaults from `.env` reaching running gateways (only the workspaces that inherit a changed default are bumped, once), route CORS settings (origins normalized, `*` never with credentials) and body limits within the ceiling, service targets (the 50 limit, the last target kept under parallel deletes, URLs once per service), health settings, one config bump per transaction, internal upstreams refused on save (IP literals in every spelling, `localhost`, `*.internal`) and the allow list, the JSON request log with `workspace_id`, config generation changes, database privileges, config triggers column by column, endpoint guards and a populated MVP database upgrade through the current migration. The tests use a fake GoTrue admin API. |
| Coverage | 99.45% (minimum 90%) |
| ruff lint and format | clean |
| mypy strict | clean |
| Run all checks | `.\backend\control-plane\scripts\check.ps1` (`-Fix` to autofix) |

**Gateway (Go)**

| Check | Result |
| --- | --- |
| Tests | about 590 passing, including the monthly quota exact under 100 parallel requests, never spent by a refused request and new each UTC month, the per-IP bucket on public routes only, wrong keys counted per IP and past the limit refusing valid keys too, the tighter bucket described, usage events written to a real Redis stream by the whole program (the last ones while it stops too), a full buffer dropping without blocking, failed writes counted with one warning per 10 s, CORS preflights and answers (allowed and refused origins and methods, backend headers replaced), 413 for announced and streamed bodies through the real forwarder, smooth weighted round robin (nginx's order for 5/1/1, exact shares over many picks), active checks against real servers with exact thresholds, passive ejection and return, balancers kept or replaced across config versions, internal upstreams refused before connecting (also through a name, and through DNS rebinding with a fake DNS server), the SSRF warning throttled per workspace and host, the workspace and consumer buckets spent together or not at all by 100 parallel requests, a slow workspace held to its in-flight share while another keeps answering, two workspaces on one path kept apart through the whole chain, config resync, a streamed answer over 64 MiB, the 1 GiB answer cap and unreadable workspaces dropped alone; the Redis tests need Docker's Redis |
| Coverage | **98.3%** total (minimum 90%, enforced): inflight, tenant, proxy, middleware and 3 more packages at 100%, egress 98.9%, upstream 98.6%, config 95.8%, server 97.4%, ratelimit 97.9%, main 87.0% |
| Fuzz tests | 9 (paths, matching, prefix stripping, request IDs, config answers, Host parsing: about 800,000 Hosts in the first 15 s run, the SSRF deny list: 2.6 million addresses in 15 s against an independent reading, and the `UPSTREAM_ALLOW` parser: 738,000 lists in 15 s), 10 s each in CI |
| Benchmarks | match **0.04 / 0.4 / 4–5 µs** for 10 / 100 / 1000 routes (no allocations); key check **0.28 µs** for 100 or 100,000 keys; Host → workspace **0.04–0.06 µs** (no allocations); whole chain without network **≈ 11 µs** with the tenant, CORS, body-limit and in-flight steps (10.3–11.5 µs over 3 runs at MT.11; 10.7–11.9 µs at MT.8; 9.9–13.2 µs at MT.7, 8.5 µs at step 3.7); a target pick **23–36 ns** for 1 to 10 targets (no allocations); an in-flight slot **23 ns** (no allocations; 79 ns with 8 goroutines on one workspace); one rate-limit check with both buckets **≈ 0.4 ms** (0.38–0.49 ms; one bucket 0.39–0.40 ms on the same machine; Windows → Docker's Redis, so a round trip dominates) |
| Auth (live) | no key 401, wrong key 401, valid 200, blocked 403, unblocked 200, revoked 401; key never in logs or at the backend |
| Rate limit (live) | limit 5/min: 5 × 200 (remaining 4 → 0), then 429 with `Retry-After: 12`; Redis stopped → requests let through, one throttled warning; limiting resumes ~3 s after Redis is back. Workspace limit 3/min (MT.8): a public route 3 × 200 then 429, and a consumer with its own budget 429 too (`limited_workspace` in the log); in-flight limit 1: `503 workspace_overloaded` with `Retry-After: 1` while a 2 s request runs, another workspace answering |
| Gateway overhead (live) | `gateway_ms` **0.06–0.19 ms** per request (Docker, single requests); under load at 300 requests/s, Redis call included, **0.36–0.38 ms median / 0.55–0.58 ms p95** (MT.12, three runs) |
| gofmt, go vet | clean |
| Race detector | clean, including the usage recorder, balancers picked while the config changes and checkers start and stop, the in-flight counters under 64 goroutines, 100 parallel two-bucket checks, 1,000 requests while the config changes every ms, and requests reading while 1,000 answers are merged (`go test -race` in a Linux container; also in CI) |
| Config propagation | a committed change served by the gateway **≈ 4 ms** after `psql` returns (10 runs, first 45 ms; MT.6, one changed workspace per answer); 68 ms admin change → gateway at step 2.5 |
| Proxy errors (live) | slow backend → **504 after 5.05 s** (route timeout 5 s); backend down → **502** |
| Image | 16.9 MB (distroless, non-root) |
| Run all checks | `.\backend\gateway\scripts\check.ps1` (`-Fix` to autofix) |

**Frontend (React)**

| Check | Result |
| --- | --- |
| Tests | 132 passing (about 80 s), the whole app rendered against a fake GoTrue and the API faked with MSW: login with and without MFA (wrong code, several authenticators), invitation and recovery links, MFA set-up and removal, password change, sign-out everywhere, my workspaces and leaving one, the role and permission rules, a suspended workspace, the API client's 401 and `user_disabled` handling; every dashboard page (FE.2): creating and editing services with targets, routes with CORS and body limits, consumers, keys shown once and revoked, plans, members and roles, the audit log and request log with filters, settings and deleting with the slug typed, APIs with their routes and OpenAPI document, the apps queue; a Viewer seeing no controls; the Platform pages (FE.3): the guard (not the Platform Admin, no authenticator, the code first), workspaces' lifecycle and limits (only changed ones sent, empty back to the default), people with their workspaces, disable and enable, developers blocked and unblocked, the platform audit log by workspace |
| Coverage | 97.5% lines (minimum 90%; functions 91%, branches 92%) |
| prettier, eslint (strict type-checked), tsc | clean |
| Browser stories | 6 pass in about 75 s (`docker compose run --rm e2e-browser`) |
| Run all checks | `.\frontend\scripts\check.ps1` (`-Fix` to format and regenerate the API types) |

**Both**

| Check | Result |
| --- | --- |
| CI | GitHub Actions on every push and pull request (`.github/workflows/ci.yml`): control plane, gateway (`-race`, 90% coverage gate, fuzz runs, image build), mock backend, then the end-to-end job on a fresh stack with a k6 smoke run |
| API contract | `backend/control-plane/openapi.json`, checked by a test |
| End-to-end tests | **66 stories pass in about 39 s**, including the Portal definition of done as one setup (sign-up through the confirmation email, the catalog with a hidden draft, an app waiting for approval and approved, an app on Free with a key, 3 calls then `429 quota_exceeded`, the developer's own usage with no quota left, the key revoked and refused, another developer seeing none of it, a deprecated API taking no new app, every developer change in the workspace's log) (P.2), a developer signing up on the portal's own GoTrue through the confirmation email in Mailpit, browsing a published API and its document, a draft and another workspace's draft `404`, and their token refused by the admin API (P.1), the v2 definition of done as one setup (plans, quota, the request log and analytics, `last_used_at`, and Prometheus showing the gateway's requests for the workspace) (V2.3), a Free consumer refused with `quota_exceeded` while a Pro one works and a move to Pro applying within a config update, one IP hammering a public route limited, 20 wrong keys getting the IP refused (V2.2), known traffic through two workspaces found exactly in each one's request log and analytics and not in the other's, with `last_used_at` moving (V2.1), the MT definition of done as one setup (two workspaces made with MFA, each with its own Owner, one person API Admin in one and Viewer in the other, `/v1/orders` on two targets each with an exact 10 / 10 split, keys, members and references never crossing, every change in its own workspace's log only, suspend, delete and restore seen at the gateway), a CORS preflight from an allowed and a refused origin, a credentialed call from a page, 413 with and without `Content-Length`, traffic over two targets by weight (exactly 30 and 10 of 40), traffic moving off a stopped target within its thresholds and back, `503 no_healthy_upstream`, a service pointing at `http://redis:6379` refused with `502 upstream_not_allowed` and internal upstreams refused on save (422), a workspace over its rate limit (429 on public and protected routes) and one with its requests in flight (503), two workspaces on their own subdomains (`Host: <slug>.localhost`) with the same path, (`docker compose run --rm e2e`), including GoTrue login with password and TOTP; nothing left behind (the e2e MFA factor is removed; the test person and the workspace `e2e-tenant` are reused; each lifecycle story's own workspace ends deleted, for the purge job); fresh stack healthy in 17 s (measured at step 4.3, before GoTrue) |
| Load test (k6) | **V2.3, everything of v2 on** (usage events, plans, quota and per-IP checks, metrics): 300 requests/s, 0 errors; the gateway adds **0.92 ms median / 1.09 ms p95**, its own work 0.39–0.40 ms median; the k6 workspace raises its own per-IP limit. V2.2, protected route (plan limit, quota check and failed-key check in the one Redis call): the gateway adds 1.05 ms median / 1.55 ms p95, its own work 0.37 ms median, 0 errors. The public run needs `PUBLIC_RATE_LIMIT_PER_IP_PER_MIN` raised (k6 sends from one IP; see loadtest/README.md). V2.1, usage events on: 300 requests/s, **0 errors**; the gateway adds **0.96–0.99 ms median / 1.24–1.29 ms p95** in clean runs, its own work 0.40–0.45 ms median (+0.03–0.05 ms for the usage event); with the usage-ingest worker and Postgres storing 300 rows/s on the same laptop some runs show p95 spikes (1.4–3.3 ms); with the worker stopped 0.91 / 1.23 ms. MT.12: in its own workspace on its subdomain with two targets (MT.12): the gateway adds **0.81–0.86 ms median / 1.10–1.20 ms p95** over three runs (0.78 / 0.83 ms at MT.8, 0.77 / 0.95 ms at step 4.4). The gateway's own work is unchanged (0.36–0.38 ms median in its log); the direct baseline's p95 was lower this time (1.34–1.41 ms against 1.71 ms), which widens the p95 difference; `docker compose run --rm loadtest` |

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
| MT.2 | Workspaces and roles, URLs under `/workspaces/{slug}`, isolation tests | ✅ |
| MT.3 | Audit log | ✅ |
| MT.4 | Members and owners | ✅ |
| MT.5 | Workspace lifecycle and quotas | ✅ |
| MT.6 | Incremental config protocol | ✅ |
| MT.7 | Gateway tenancy | ✅ |
| MT.8 | Workspace limits in the gateway | ✅ |
| MT.9 | SSRF protection | ✅ |
| MT.10 | Load balancing and health checks | ✅ |
| MT.11 | CORS and body size limit | ✅ |
| MT.12 | MT end to end, load test, docs | ✅ |

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

**Status:** ✅ Done (Nov 21). Commits `04f3aab` (`updated_at` trigger), `b1a984f` (GoTrue and Mailpit in the stack), `17bccf1` (GoTrue login, `users` replace `admins`, disabling users), then the docs update (`a567a15`). Review fixes: `85fddbb` (users bound to their GoTrue account; locked, failure-safe disable/enable), `42f8427` (Compose starts without a Platform Admin password; Mailpit on 127.0.0.1), `7a7d885` (populated MVP upgrade test, `.env` upgrade guide), the docs update `384b1f4`; second review: `c2c144d` (a bound admin is never rebound automatically; `scripts/relink_user`), `f8cb671` (a failed GoTrue lookup keeps a disable), the docs update `d33613e`; then `02f65d8` (Platform Admin granted only on a verified or first binding; relinking a disabled user needs GoTrue's confirmed ban) and `f74a6ae` (a flaky rate-limit test fixed). Every "done when" item is covered by e2e stories (`e2e/test_identity.py`) and unit tests. GoTrue `v2.197.0`, Mailpit `v1.33.0`. Choices made while building are in the decisions log (MT.1 rows).

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

**Status:** ✅ Done (Nov 21). Commits `d499329` (memberships, per-workspace prefixes, slug rules, `default`-only gateway config), `86edfc4` (workspace-scoped API with `require_permission`, workspace endpoints, guard and isolation tests, e2e), the docs update `ba636ac`; review fixes `acf0a7c` (the new owner's row locked during creation), `f20fcb8` (permission guard against the plan's matrix, with valid requests), `fb253f0` (422 for a route filter naming another workspace's service), then the docs update. The guards were checked to fail on purpose: a DELETE declared with a read permission, and an endpoint under `/workspaces/{slug}` without one. Viewers and API Admins exist only in unit tests until MT.4 adds the members API.

### MT.3 Audit log [11]

**Outcome:** every admin change leaves a record of who, what, when and before/after.

- `audit_events` table; the application's database user gets only `INSERT` and `SELECT` on it
- `audit.record(...)` written in the same transaction as each change; `changes` holds changed fields only; no secrets (keys by prefix only)
- Every existing write endpoint records its event (`service.create`, `route.update`, `key.revoke`, ...)
- **Audit guard test:** every mutating endpoint writes exactly one event; a failed change writes none
- `GET /api/v1/workspaces/{slug}/audit-events` (`audit:read`) and `GET /api/v1/audit-events` (Platform Admin), with filters and pagination
- Retention job (default 365 days) running as a separate database user

**Done when:** the guard test covers every mutating endpoint; a rolled-back change leaves no event; Platform Admin actions in a workspace appear in that workspace's log with `actor_type = platform_admin`.

**Status:** ✅ Done (Nov 21). Commits `4948c41` (the API on its own least-privilege database login), `40aacae` (audit events for every write, request ids, the audit guard), `7c68e46` (workspace and platform audit endpoints), `4d04860` (retention job as its own login and container), then e2e and docs. The rollback test was checked to fail on purpose (an event committed on its own survives a refused change). Retention was tested against the real database as `apim_retention`, not end to end (it would need year-old events).

### MT.4 Members and owners [8, 9]

**Outcome:** Owners build their team; a workspace can never lose its last owner.

- `POST /members {email, role}`: existing user → member at once; new person → GoTrue invite email (Mailpit) → member at first login; existing member → 409
- `PATCH` (change role) and `DELETE` (remove) by Owners; `DELETE /api/v1/me/workspaces/{slug}` (leave)
- Owner rules: several owners; nobody changes their own role; the last active human owner can't be demoted, removed or leave (`409 last_owner`); owner rows locked with `SELECT ... FOR UPDATE`
- Platform Admin can assign an owner in any workspace (audited)
- `POST /api/v1/workspaces` now accepts a new `owner_email` (invites them)
- Audit: `member.add`, `member.role_change`, `member.remove`, `member.leave`, `member.owner_assigned_by_platform`

**Done when:** an invitation email arrives in Mailpit and leads to a working membership; a concurrency test with two owners demoting each other at the same moment always leaves one owner; all rules above have tests.

**Status:** ✅ Done (Nov 21). Commits `29b1768` (a new owner invited on workspace creation), `817e8ca` (members API with owner rules and row locks), then e2e and docs. The concurrency tests widen the race window and were checked to fail without the owner-row lock (both owners demoted, or both left). e2e accepts a real invitation from Mailpit, sets a password and logs in.

### MT.5 Workspace lifecycle and quotas [6, 10]

**Outcome:** workspaces can be suspended, deleted, restored and purged; each has adjustable limits.

- `workspaces`: `status`, `status_reason`, `deleted_at`, `purge_after`, limit columns (NULL = platform default)
- Suspend / unsuspend (Platform Admin): writes → `403 workspace_suspended`, reads still work
- Delete `{confirm: "<slug>"}` (Owner or Platform Admin) → hidden from members; restore within 30 days (Platform Admin)
- Daily purge job deletes all workspace data after 30 days, keeps the row with status `purged`; slugs are never reused; `default` can't be suspended or deleted
- Quotas (services, routes, consumers) → `409 quota_exceeded`; `PATCH /limits` (Platform Admin)
- Lifecycle audit events as platform-level events (`workspace_id` NULL)

**Done when:** every transition and its refusals are tested; a purged workspace's slug can't be taken again; quotas are enforced under parallel creation (no overshoot).

**Status:** ✅ Done (Nov 21). Commits `96dab16` (status, deletion times and limit columns with their CHECKs), `608eece` (lifecycle API: suspend, unsuspend, delete with `confirm`, restore, rename; suspended workspaces read-only, deleted ones 404), `d606631` (quotas and `PATCH /limits`), `71e8e15` (purge job with its own login `apim_purge` and container `workspace-purge`), `fa0a2ab` (e2e stories), then the docs. The parallel-creation test (10 at once against a quota of 3) was checked to fail without the workspace-row lock, 3 runs out of 3. Also `c25700f`: the gateway's real-Redis test failed once more (`TestServeRateLimitsWithRealRedis`, "Redis not reachable: i/o timeout" in its setup, on a busy machine); its setup now retries for ~2 s instead of using the 50 ms production timeouts. The purge job was run against the real stack (`--once`), not end to end on a 30-day-old workspace. Choices made while building are in the decisions log (MT.5 rows).

### MT.6 Incremental config protocol [7]

**Outcome:** a change in one workspace sends only that workspace to the gateways.

- Control plane: `config_seq` replaces `config_version`; `workspace_config (version, seq)`; statement-level triggers with transition tables bump the touched workspaces (now also on `workspaces` changes). Follow the Postgres rules in PLAN.md section 5: three triggers per table (no `OR` with transition tables) and config-column filtering inside the function (no `UPDATE OF` with transition tables); an update that changes only `updated_at` or `last_used_at` bumps nothing (test)
- `/internal/config?since=&wait=` with `{seq, full, workspaces, deleted}` exactly as in PLAN.md section 6; effective limits in the answer; per `(workspace, version)` cache; `?workspaces=` filter
- Gateway: snapshot becomes `ByHost` + `Workspaces`; merge that reuses unchanged workspaces; `full` handling and the protocol-error fallback to `since=0`
- Until MT.7 the gateway still routes every request to `default`, so all e2e stories keep working

**Done when:** a change in workspace A produces an answer containing only A (test); gateway start, database reset and deleted workspaces are covered by tests; the existing race test (requests during constant config changes) passes with `-race`; config propagation is still measured in milliseconds.

**Status:** ✅ Done (Nov 22). Commits `ae34129` (control plane: `config_seq`, `workspace_config`, the transition-table triggers, the incremental `/internal/config` with its per-workspace cache), `1560e53` (gateway: per-workspace snapshot, merge, `since`, the protocol-error fallback), then the docs. The triggers are tested column by column (every config column bumps, every other column doesn't, and a guard makes a new column be decided). A change in one workspace answers only that one; gateway start (`since=0`), a database reset (`since` from the future), deleted, purged, suspended and restored workspaces have tests on both sides. The race test passes with `-race`, plus a new one merging 1,000 answers under readers. All 32 e2e stories pass on the rebuilt stack, and its log shows partial answers merged.

### MT.7 Gateway tenancy [1, 10, 17]

**Outcome:** the gateway serves each workspace on its own subdomain and never mixes them.

- `tenant` middleware: Host → workspace (`GATEWAY_BASE_DOMAIN`, port dropped, bare domain → `default`); `404 workspace_not_found`; `403 workspace_suspended`
- Route matching and key lookup inside the workspace only; a key of another workspace → `401 invalid_api_key`
- Strip client `X-Workspace-ID`, set the trusted one
- `workspace_id` on every gateway log line and every control-plane log line
- Fuzz test for host parsing

**Done when:** e2e shows two workspaces with the same `/v1/orders` reaching different backends via `acme.localhost` and `globex.localhost`; a key used on the other workspace's host gets 401; a suspended workspace gets 403 within one config update.

**Status:** ✅ Done (Nov 22). Commits `965e931` (gateway: the `tenant` package and middleware, routing and keys inside the Host's workspace, trusted `X-Workspace-ID`, `workspace_id` in the log, `GATEWAY_BASE_DOMAIN`, the Host fuzz test), `ce78850` (control plane: one JSON request line per call and `request_id` / `workspace_id` on every app log line), `302354a` (e2e), then the docs. e2e makes two fresh workspaces per run (`e2e-a-…`, `e2e-b-…`) with the same `/v1/orders`: each Host reaches its own backend with its trusted `X-Workspace-ID`, the other workspace's key is 401, an unknown subdomain 404, and a suspended workspace 403 within one config update while the other keeps answering. A whole-chain Go test checks the same isolation, and the live logs of both services show `workspace_id`.

### MT.8 Workspace limits in the gateway [6]

**Outcome:** one workspace can't take the gateway from the others.

- Lua script with the workspace bucket and the consumer bucket in one call (spend only if both allow); public routes use the workspace bucket; log `limited_workspace`
- In-flight semaphore per workspace per instance → `503 workspace_overloaded` with `Retry-After: 1`
- Fail-open behavior unchanged when Redis is down

**Done when:** Redis tests show both buckets, refusal by either, and atomicity under 100 parallel requests; a slow backend in workspace A can't use more than its in-flight share while workspace B keeps answering (test); e2e covers the workspace rate limit.

**Status:** ✅ Done (Nov 22). Commits `8578cf1` (the two buckets in one Lua script, `buckets.lua`; public routes limited by the workspace; `limited_workspace` in the log; workspaces without valid limits dropped), `624dc02` (the `inflight` package and step: `503 workspace_overloaded` with `Retry-After: 1`), `7d762bb` (e2e), `dd82c93` (the load test raises `default`'s limit with MFA), then the docs. Redis tests cover both buckets, refusal by either without spending the other, the longer wait named, and 100 parallel requests from 5 consumers in one workspace: exactly the workspace's 50 allowed, and every consumer's bucket lost exactly its allowed requests. The whole-chain test fills a slow workspace's 3 slots: its 4th request is 503 at once while another workspace answers 20 requests; it was checked to fail without the step (the 4th request hung until a 504). e2e covers the workspace rate limit on a public and a protected route and the in-flight limit with a 2 s request; the live log shows `limited_workspace`. Fail-open is unchanged (the same `unavailable` path; the in-flight limit needs no Redis).

### MT.9 SSRF protection [3]

**Outcome:** no tenant can make the gateway reach internal addresses.

- `egress` package: IP policy (default deny list from PLAN.md section 7, `UPSTREAM_ALLOW` exceptions), guarded `net.Dialer` with the `Control` hook; IPv4-mapped IPv6 handled
- `502 upstream_not_allowed` and a warning log with workspace and target
- Control plane: scheme check and early 422 for internal IP literals and known internal names on save
- Compose and CI set `UPSTREAM_ALLOW=mock-backend`
- Fuzz test for the policy; a DNS-rebinding test (a name resolving to an internal IP at connect time is refused)

**Done when:** e2e shows a service pointing at `http://redis:6379` refused with `upstream_not_allowed`; all other e2e stories pass with the allow list.

**Status:** ✅ Done (Nov 22). Commits `1d0c7e0` (the `egress` package: deny list, allow list, the dialer with the `Control` hook, fuzz and DNS-rebinding tests), `8d396bd` (the gateway dials through it: `502 upstream_not_allowed` and a warning with workspace, route, host and address; `UPSTREAM_ALLOW` in settings and Compose), `2d83b8f` (the control plane's 422 on save), `d8ee7fb` (e2e, the fuzz test in CI), then the docs, then the review fixes (`215771d` gateway, `42355dd` control plane, then these docs). The live log shows `redis` refused at `172.18.0.2`. All 42 e2e stories pass with `UPSTREAM_ALLOW=mock-backend`, and the k6 smoke run passes through the guarded dialer. The race detector is clean on Linux, including the DNS-rebinding test.

### MT.10 Load balancing and health checks [14]

**Outcome:** a service runs on several backend instances; traffic avoids broken ones.

- `service_targets` table; migration turns each `upstream_url` into one target; target endpoints; health-check settings on services
- Gateway `upstream` package: smooth weighted round robin, active checks (interval, thresholds), passive ejection, `503 no_healthy_upstream`, `target_id` in the log
- Balancer and health state survive config updates when a service's targets didn't change; checkers stop when targets leave
- Health checks use the SSRF dialer
- Compose gets a second mock-backend instance for tests

**Done when:** unit tests show the weighted distribution over many picks, skipping, recovery and passive ejection; e2e shows traffic spread over two targets and moving off a stopped one within the configured thresholds.

**Status:** ✅ Done (Nov 22). Commits `9335e49` (control plane: `service_targets`, the migration moving each `upstream_url` into one target, health settings, target endpoints, the config answer, one bump per transaction), `d04d3af` (gateway: the `upstream` package with the balancer, active checks, passive ejection and the registry; the forwarder picks per request; `target_id` in the log; `503 no_healthy_upstream`), `dab224f` (a second mock backend and the e2e stories), then the docs. The migration was run down and up on the local database (53 services, each now with its target). Unit tests cover the weighted distribution (exact shares, nginx's smooth order), skipping, recovery and passive ejection; e2e shows exactly 30 / 10 over two targets and traffic moving off a stopped one within the thresholds; the live log shows `target set aside after failed requests` and `target_id`. Building it found two things fixed on the way: one service with its targets bumped the config twice (now once per transaction), and targets sent in one request came back in random order (now in the order sent).

### MT.11 CORS and body size limit [15, 16]

**Outcome:** browser apps can call APIs through the gateway; oversized bodies never reach a backend.

- Route CORS settings; preflight answered by the gateway before auth, not forwarded, not rate-limited; headers on normal answers (`Vary: Origin`, no `*` with credentials)
- Route `max_body_bytes` with platform default and ceiling (control-plane settings; effective value in the snapshot); `413 request_too_large` from `Content-Length`; `MaxBytesReader` for bodies without a length

**Done when:** e2e covers a preflight from an allowed and a refused origin, a credentialed call, and a 413 both with and without `Content-Length`.

**Status:** ✅ Done (Nov 22). Commits `7f526cd` (control plane: route `max_body_bytes` and `cors`, the two settings, the config answer, the routes' trigger), `9bd5020` (gateway: the `cors` and `bodylimit` steps, path-only matching for preflights, `body_limit` in the log), `90b218b` (e2e), `1623334` (a proxy test for the cut body), then the docs. e2e: a preflight from the allowed origin is answered by the gateway without a key (204, the allow headers, nothing forwarded), one from another origin gets no allow headers, a credentialed call gets `Access-Control-Allow-Origin` and `-Credentials`, and a body over the limit gets 413 with and without `Content-Length` while one at the limit passes. The migration was run down and up on the local database.

### MT.12 MT end to end [all]

**Outcome:** the MT definition of done (PLAN.md section 1) is proven on every push.

- e2e stories for everything in PLAN.md section 11 "MT adds"; invitation emails read through Mailpit's API
- k6 load test updated for host-based routing and two targets; numbers recorded here
- README, `docs/DEVELOPMENT.md` and this file updated (quality tables, commands, new services)
- Decisions taken during MT added to the decisions log below

**Done when:** CI is green on a fresh stack, including the MT e2e stories and the k6 smoke run; the gateway overhead measured again and written down.

**Status:** ✅ Done (Nov 22). Commits `2bc9f7c` (the flaky `TestSyncDropsWhatLeftTheConfig` counts checks where they leave the gateway), `a08eb60` (a changed platform default reaches running gateways), `21a5ed4` (the MT definition of done as an e2e module), `ee70859` (k6 in its own workspace on two targets), then the docs. Every item of PLAN.md section 11 "MT adds" has an e2e story: login and MFA (`test_identity`), two workspaces on one path and the wrong host's key (`test_tenancy`, `test_mt_done`), roles (`test_members`, `test_mt_done`), last-owner rules (`test_members`), suspend and restore (`test_lifecycle`, `test_tenancy`, `test_mt_done`), audit entries for each change (`test_audit`, `test_mt_done`), the workspace rate limit (`test_workspace_limits`), SSRF (`test_ssrf`), two targets and a stopped one (`test_load_balancing`, `test_mt_done`), CORS and 413 (`test_cors_body_limit`), invitations through Mailpit's API (`test_members`). All 56 stories pass in about 22 s. k6: 0.81–0.86 ms median and 1.10–1.20 ms p95 added at 300 requests/s, three runs, 0 errors. The flaky test passed 200 runs in a row after the fix. The platform-defaults fix was checked live: restarting the control plane with `WORKSPACE_MAX_CONCURRENT=150` sent the running gateway a partial answer with the two workspaces that inherit it.

## v2: Usage, plans and metrics

**What and how:** PLAN.md (sections 1 and 3–11; marked V2.x). **Why:** the decisions log below (the "Plan" rows of Nov 22). Backend only: the pages come in FE.

**How these steps are worked:** fewer, larger steps than MT, because an AI agent builds them. Each step is one self-contained piece of work across the gateway, the control plane, migrations, tests, e2e and docs. The agent plans its own sub-tasks, commits logically along the way, and hands back only when every "done when" item holds and both `check.ps1` scripts and the e2e tests are green. Rules in `AGENTS.md`.

| # | Step | Status |
| --- | --- | --- |
| V2.1 | Usage and analytics | ⬜ |
| V2.2 | Plans and protection | ⬜ |
| V2.3 | Metrics and v2 end to end | ⬜ |

### V2.1 Usage and analytics

**Outcome:** every request through the gateway is recorded, and a workspace's members see who calls what, how often, how many errors and how fast.

- Gateway `usage` package: one event per proxied request (the log line's fields plus an `event_id`), buffered briefly and written with pipelined `XADD usage:events MAXLEN ~`; never delays a request; Redis down → events dropped and counted, throttled warning
- `request_logs` partitioned by month (PLAN.md section 5), primary key (`event_id`, `ts`); retention by dropping partitions (`REQUEST_LOG_RETENTION_DAYS`, default 90); the purge job covers it
- `usage-ingest` container with its own login `apim_ingest`: consumer group, batch insert with `ON CONFLICT DO NOTHING`, `last_used_at` (at most once a minute per key), commit, then `XACK`; entries pending on a stopped worker claimed again (`XAUTOCLAIM`)
- `usage:read` (every role); analytics endpoints (requests, errors, latency percentiles over time; by route and by consumer) and the request-log endpoint with filters and paging
- Decide and record how monthly partitions are created and dropped (it needs the table's owner, never the API's login)

**Done when:** e2e sends known traffic through two workspaces and finds exactly that many rows in each workspace's request log, the same numbers in its analytics, and nothing of the other's; a worker stopped mid-batch and restarted loses and doubles nothing (test); with Redis stopped, requests still answer at normal speed and the dropped events are counted; `last_used_at` moves; the gateway's overhead in the load test stays in its MT.12 range.

**Status:** ✅ Done (Nov 23). Commits `9e8db2e` (gateway: the `usage` package, its own Redis client from the new `redisconn` package, the recorder started and stopped around the server), `62b4bf0` (control plane: `request_logs` by month with the partition functions, `apim_ingest`, the usage-ingest worker, the purge and retention), `a9a3977` (analytics and request-log endpoints, `usage:read`, the isolation guard per filter), `0099ca6` (the worker no longer imports the API's settings: it crashed at start in Compose), `5289ed7` (e2e), `99101f2` (smaller, more frequent usage batches), then the docs. Every "done when" item: the e2e story finds exactly the sent requests (5 good and 2 wrong-key requests in one workspace, 3 in the other) in each log and its analytics and none of the other's, and the key's `last_used_at` moves; a worker stopped mid-batch and another claiming its entries lose and double nothing (test); with Redis stopped live, 50 requests answered in 228 ms in total and the gateway warned `usage events dropped` with the count; the race detector is clean. **The load test is slightly above the MT.12 range** (0.96–0.99 ms median against 0.81–0.86): the gateway's own work grew by 0.03–0.05 ms for the event, and the rest is the worker and Postgres storing 300 rows/s on the same laptop (the same test with the worker stopped: 0.91 / 1.23 ms). Live, the worker drained 324 events written before it ran and stored 38,684 during the load tests, 0 repeated, 0 dropped.

### V2.2 Plans and protection

**Outcome:** an API owner sells tiers: each consumer is on a plan with a rate limit and a monthly quota, and abusive clients are held back.

- `plans` per workspace (PLAN.md sections 5 and 6), `plans:read` / `plans:write` (API Admin and Owner write); one default plan per workspace; a plan above the workspace's effective limit → `422`; a plan with consumers can't be deleted (`409`)
- `consumers.plan_id` replaces `rate_limit_per_min`; the migration makes `Default` and `Legacy <n>/min` plans so no consumer's limit changes; plans and `consumers.plan_id` bump the config; keys in the config carry `plan_id`, `rate_limit_per_min`, `monthly_quota`
- No per-consumer override (a special customer gets its own plan); the workspace ceiling stays the Platform Admin's (MT.5)
- Gateway: the monthly quota in the same Lua call as the buckets (`quota:{consumer_id}:{yyyymm}`, UTC months), `429 quota_exceeded` with `Retry-After` and `X-Quota-*`; a per-IP bucket on public routes; failed key attempts per IP → `429 too_many_failed_attempts` for a while; a log reason for each
- Audit: `plan.create`, `plan.update`, `plan.delete`

**Done when:** e2e: a Free consumer gets `quota_exceeded` after its (small, for the test) quota while a Pro consumer keeps working; moving a consumer to another plan applies within one config update; a plan above the workspace's limit is `422`; one IP hammering a public route gets 429 while another passes; repeated wrong keys from one IP get `too_many_failed_attempts`. Redis tests: the quota is exact under 100 parallel requests and is never spent by a refused request. The populated-database upgrade test shows every consumer's limit unchanged.

**Status:** ✅ Done (Nov 23). Commits `418c5dc` (control plane: plans, consumers on plans, the migration, the per-IP settings in the config), `2de4752` (gateway: `limits.lua` with the quota, the per-IP bucket and the failed-key refusal; `failures.lua`), `7a30cfa` (e2e and k6 on plans), `dd862cd` (the tighter bucket described; e2e runs start with no failed keys), then the docs. Every "done when" item holds: the e2e stories (quota, plan move, 422 above the workspace, the hammering IP, 20 wrong keys); the quota exact under 100 parallel requests and never spent by a refused request (Redis tests); the populated upgrade with every consumer's limit unchanged. "Another IP passes" is shown by the Redis tests only: the e2e container has one address.

### V2.3 Metrics and v2 end to end

**Outcome:** the platform can be watched, and the v2 definition of done is proven on every push.

- Gateway `metrics` package (the official Prometheus Go client) on its own port (`METRICS_ADDR`): requests, latency histograms, rate-limit refusals by reason, target health, in-flight, config sequence and last sync, dropped usage events (PLAN.md section 7); bounded labels
- Control-plane metrics for `usage-ingest` (lag, batches); a `prometheus` service in Compose scraping both
- An e2e module playing the v2 definition of done (PLAN.md section 1); k6 again with usage events on, numbers recorded here
- README, `docs/DEVELOPMENT.md` and this file updated

**Done when:** Prometheus in Compose shows the gateway's counters moving during the e2e run; the v2 module passes in CI on a fresh stack; the gateway overhead with usage events is measured and written down.

**Status:** ✅ Done (Nov 23). Commits `d7e296d` (gateway: the `metrics` package and its port), then the control plane (a workspace's own per-IP limit; the worker's metrics), Compose (Prometheus) with the e2e module and k6, then the docs. Every "done when" item: the v2 module passes, and its last check reads the gateway's requests for its workspace from Prometheus's query API (so Prometheus scraped the counters during the run); the overhead with usage events on is measured above. Added at the owner's request: `ip_rate_limit_per_min` per workspace (V2.2's open decision).

## Portal: developer portal

**What and how:** PLAN.md (sections 1, 5 and 6; marked P.x). Worked like v2.

| # | Step | Status |
| --- | --- | --- |
| P.1 | Developers and catalog | ✅ |
| P.2 | Self-service and Portal end to end | ✅ |

### P.1 Developers and catalog

**Outcome:** developers sign up on their own and browse what each workspace publishes.

- `auth-portal`: a second GoTrue with its own `auth_portal` database and login, open sign-up with email confirmation (Mailpit); the control plane verifies its tokens separately; a guard test proves a portal token never opens the admin API and an admin token never opens the portal API
- `developers` (just in time, never mixed with `users`)
- `apis` with `api_routes`, an optional OpenAPI document (size-limited) and the lifecycle `draft` → `published` → `deprecated`; `apis:read` / `apis:write`; audited
- Portal catalog endpoints: a workspace's published and deprecated APIs with their docs; drafts `404`

**Done when:** e2e signs a developer up through the confirmation email in Mailpit, logs in, sees a published API and its document, gets `404` for a draft and for another workspace's draft, and is refused by the admin API with the same token.

**Status:** ✅ Done (Nov 23). Commits `2e0c07d` (the developers' GoTrue), `9800b8d` (developers, APIs and the catalog), `fbd151f` (e2e), `453ebdc` (the rate-limit test fix), `98f06b4` (docs). In order: the developers' GoTrue (`auth-portal`, its own `auth_portal` database and login, open sign-up, its own audience and secret) with the control plane verifying its tokens apart; developers, APIs and the portal catalog (`developers`, `apis`, `api_routes`, the Platform Admin's block, the guard tests both ways); the e2e story; the rate-limit test fix below; the docs. Every "done when" item holds in `test_portal_catalog.py` (plus: an unconfirmed developer can't log in, a member's token is refused by the portal). Also fixed: `TestConcurrentRequestsSpendBothBucketsAtomically` (gateway) failed about once in 20 runs because the test measured time with its own clock while the script refills by Redis's, and Docker Desktop steps its VM's clock now and then; the timing checks now allow 250 ms between the two clocks (60 runs under load: no failure).

### P.2 Self-service and Portal end to end

**Outcome:** a developer goes from sign-up to a working key without talking to anyone, and the backend is complete.

- `apps` backed by consumers (PLAN.md section 5): created in a workspace on a portal-visible plan; a plan with `requires_approval` makes the app `pending` (consumer blocked) until an API Admin approves (`apps:write`); deprecated APIs take no new apps
- Developer keys on their apps (the admin API's key rules), own usage and quota from V2.1 and V2.2
- Developer writes audited in the workspace's log (`actor_type` `developer`)
- An e2e module playing the Portal definition of done (PLAN.md section 1)
- A backup and restore script: dump, restore into a fresh stack, rotate the config generation; tried for real and documented in `docs/DEVELOPMENT.md`

**Done when:** the Portal module passes in CI on a fresh stack; a developer never sees another developer's apps (an isolation guard over the portal endpoints); a restored backup serves the same config at the gateway. With this step the backend is complete.

**Status:** ✅ Done (Nov 23). Commits: apps backed by consumers with approval, keys and usage (control plane: `apps`, the plans' portal flags, the developers' block reaching their apps, the portal guard tests); the e2e module `test_portal_done.py`; the backup and restore scripts; the docs. Every "done when" item: the Portal module passes locally on the running stack and runs in CI's e2e job on a fresh stack with every push of this branch; `test_portal_guards.py` checks every portal endpoint with an app or key id against another developer's (`404`); the backup was restored for real into a fresh Compose project, where a key made before the backup answered `200` at the restored gateway and the full config answer was identical apart from its rotated generation (DEVELOPMENT.md, "Backup and restore"). **The backend is complete.**

## Out of plan

Decided Nov 22, 2026: not scheduled, kept in PLAN.md for reference and marked *Out of plan*: JWT auth, transforms, response caching, circuit breaker (half-open), LLM gateway mode, webhooks, OpenTelemetry tracing, a Grafana dashboard, deployment to a server (Caddy, TLS, wildcard DNS) and workspace tiers. The deferred items in MT_ROLES_DECISIONS.md stay as recorded there.

## FE: Frontend (after the Portal)

**The whole frontend is built after the Portal, all at once**, once the backend is complete, by Claude, against the final control-plane API and its committed `openapi.json`. Pages and areas are listed in PLAN.md section 8. The app is called by its three areas: **Dashboard** (workspace members, by role), **Platform** (Platform Admin) and **Developer portal**; not "Admin UI", because most of its users are not admins.

| # | Step | Status |
| --- | --- | --- |
| FE.1 | Foundation: Vite, React, TypeScript, layout, GoTrue login and MFA, account page, workspace switcher, role-aware routing and buttons, generated API types (Vite proxies `/api`, so no CORS is needed) | ✅ |
| FE.2 | Dashboard: Overview, Services and service detail (targets, health), Routes (CORS, body limit), Consumers and keys, Members, Activity, Settings, Plans, Logs | ✅ |
| FE.3 | Platform: Workspaces (create, suspend, restore, limits), Users (disable, enable), platform activity | ✅ |
| FE.4 | Developer portal: sign-up and login, API catalog, API docs with try-it, own keys, own usage | ⬜ |

The FE steps are built on stacked branches (`fe1-foundation` → `fe2-dashboard` → `fe3-platform` → `fe4-portal`), each one a pull request into the one before. The page lists above are a sketch: every feature of the finished backend gets a page or a control (decided Nov 23, 2026).

### FE.1 Foundation

**Done when:** a member and the Platform Admin can sign in (with the authenticator code when they have one), choose a password from an invitation or recovery link, set up and remove MFA, see their workspaces with their role, leave one, and open a workspace's dashboard frame; the frontend checks pass with coverage ≥ 90%; browser stories run on the stack and in CI.

**Status:** ✅ Done (Nov 23). Commits `916bd32` (scaffold), `1cf1f03` (login, MFA, account, workspaces, layout), `d9bff87` (choosing between authenticators), `e79d70d` (types keep defaulted fields optional), `a9a0fd9` (the `frontend` service), `a11441c` (browser stories), `3eefffa` (CI). Checked by hand in a browser against the real stack: sign-in, MFA set-up with the QR code, sign-in with the code, removing the factor.

### FE.2 Dashboard

**Done when:** a workspace member can do everything their role allows from the dashboard: services and targets, routes (CORS, body limit), consumers and keys, plans, APIs and the apps queue, members, settings, and read the overview, activity and logs; a Viewer sees every page without controls; tests and checks pass.

**Status:** ✅ Done (Nov 23). Commits `e45e3ce` (overview, services, routes, consumers, keys, plans, shared tables and dialogs), `5efc60e` (members, activity, logs, settings), `5f5a65b` (APIs and apps), `548134f` (browser stories). Checked by hand against the real stack: a service, route, consumer and key made in the UI, 11 calls through the gateway, then the overview (11 requests, 27% errors, 1 active consumer), the chart, the logs and the activity showing them.

### FE.3 Platform

**Done when:** the Platform Admin, signed in with MFA, can create, suspend, unsuspend, delete and restore workspaces and change their limits, find people with their workspaces and disable or enable them, block or unblock developers, and read the platform's audit log; anyone else is kept out; tests and checks pass.

**Status:** ✅ Done (Nov 23). Commits `2c926ad` (the `GET /api/v1/users` endpoint with tests and openapi.json), `632f3a9` (the Platform pages), `8c249ea` (browser story), then docs. The story runs against the real stack: the admin signs in with the authenticator code, creates a workspace, suspends, unsuspends and deletes it, and finds a new member with their workspace and role on the Users page.

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
| Nov 21 | MT.1 | Timing tests against the token bucket assert on the response that proves the point (e.g. the denial that ended a drain), never on an extra call afterwards, and allow for every token a slow run can earn | The bucket refills continuously, so an extra call can rightly get a token that arrived during its Redis round trip: `TestBucketRefills` failed about 1 run in 120 for that reason |
| Nov 21 | MT.2 | Until MT.6/MT.7, `/internal/config` carries only the `default` workspace | Path prefixes are unique per workspace from MT.2, but the gateway doesn't know workspaces yet: another workspace's routes and keys would work on its bare host, across tenants |
| Nov 21 | MT.2 | A Platform Admin acting beyond their own role in a workspace (or as a non-member) needs MFA, like the platform endpoints; within their own role, no MFA | That power reaches every tenant; the migrated admin, Owner of `default`, keeps working with a password login (k6, e2e) |
| Nov 21 | MT.2 | Startup makes the first Platform Admin Owner of `default` when it has no Owner (the migration does the same for an upgraded install) | A fresh install's `default` would otherwise have no Owner; a workspace always keeps one |
| Nov 21 | MT.2 | Slug rules (DNS label, 3–50 chars, reserved names → 422) and the slug CHECK come with MT.2 instead of MT.5 | Workspaces can be created from MT.2 on |
| Nov 21 | MT.2 | 403 for a role without the permission is `permission_denied`; `GET /api/v1/workspaces/{slug}` (`workspace:read`) added now | A code the dashboard can act on, like the other 403s; the plan lists the endpoint, and it gives `workspace:read` a user |
| Nov 21 | MT.2 | `owner_email` is checked for shape only (not `EmailStr`) | It must only name an existing user, and the lookup is the real check; `email-validator` refuses addresses GoTrue accepts (e.g. `*.test`) |
| Nov 21 | MT.2 | Guard tests read each endpoint's permission from its dependencies (`PermissionCheck` is a class with `.permission`); writes must declare a non-read permission; isolation fails if a new id parameter or `..._id` body field appears untested | New endpoints can't skip the checks silently |
| Nov 21 | MT.2 | e2e reuses its workspace `e2e-tenant` (Owner: the test person) | Workspaces can be deleted only from MT.5; everything created inside it is deleted |
| Nov 21 | MT.2 | Workspace creation share-locks the owner's row (`FOR SHARE`) from the "is active?" check to the commit (after review) | A parallel disable (which locks it `FOR UPDATE`) could otherwise commit in between and leave a new workspace with only a disabled Owner; now it finishes first (creation answers 422) or waits. Disabling an Owner afterwards stays allowed: the last-owner rules (MT.4) cover demoting, removing and leaving |
| Nov 21 | MT.2 | The permission guard's expectations are a matrix copied by hand from PLAN.md, the production map must equal it, and allowed cases send valid requests that must succeed (after review) | Expectations taken from the map under test would expect a wrong grant instead of catching it |
| Nov 21 | MT.2 | A list filter naming an object not in this workspace (`?service_id=`) answers 422, for another workspace's object and for an unknown one alike; the isolation guard also discovers `..._id` query parameters (after review) | The same answer as for a body reference; it was an empty 200 before, which leaked nothing but broke the "422 when referencing B's objects" rule |
| Nov 21 | MT.3 | The API runs as `apim_app`, migrations and setup as the owner (`MIGRATION_DATABASE_URL`), retention as `apim_retention`; `scripts/prepare_database.py` makes the logins on every start, migrations grant the privileges | The owner is a superuser and ignores grants, so "the API may only INSERT and SELECT audit events" can't hold otherwise. Roles are server-wide with passwords, so they live outside migrations; privileges are schema, so they live in them. Never dropped on downgrade: `apim_test` shares the server |
| Nov 21 | MT.3 | Tests run as `apim_app` too, and clean up and backdate events as the owner | The privileges are tested the way production uses them |
| Nov 21 | MT.3 | One event per change, none when nothing changed; actions are `<target>.<verb>`, lifecycle events as PLAN.md names them (`workspace.created`); user changes, workspace creation and startup's changes are platform-level (`workspace_id` NULL) except startup's `member.add` in `default` | An event per request that changed nothing would bury the real ones; PLAN.md's own names kept |
| Nov 21 | MT.3 | Also audited: a person's first sign-in (`user.create`, by themself), startup's changes and `relink_user` (`system`) | "Every write records its audit event" (AGENTS.md) |
| Nov 21 | MT.3 | `X-Request-ID` on every control-plane response (the client's when safe, gateway rules), stored with each event; a pure ASGI middleware | One change can be followed from the client to its entry; pure ASGI leaves the held `/internal/config` requests untouched |
| Nov 21 | MT.3 | `audit_events` has no `updated_at` and no foreign keys; the `?actor=` filter takes an email | Events never change and must outlive their actor, target and workspace; people search by email |
| Nov 21 | MT.3 | The retention job is its own Compose service (`audit-retention`) with only its login and the period, deleting in batches of 5000 once a day; a failed round is retried the next day | It never holds the API's secrets, and the API never holds a login that can delete events |
| Nov 21 | MT.3 | The audit guard's rollback test makes Postgres refuse the change itself (a temporary trigger), not every commit | Refusing every commit would also pass with an event committed on its own; this way only one shared transaction passes |
| Nov 21 | MT.3 | Run migrations and test setup separately from the API container. Strip upstream URL credentials from audit events, lock key rows during revocation and preserve the request ID on unhandled 500 responses | The running API has no privileged database credentials. Concurrent revocations produce one event, and clients can correlate unexpected errors |
| Nov 21 | MT.3 | `upstream_url` with a username or password is refused (422); audit events still strip credentials from services saved before | A password there was stored, returned by `GET` (to Viewers too) and sent to the gateway, which never uses it. Secrets never appear in API answers (AGENTS.md) |
| Nov 21 | MT.4 | A new person's `users` row and membership are made when they are added, bound to the GoTrue account the admin API finds or invites; no invitations table; an existing GoTrue account gets no second email | Decision 8's simple flow: they are a member from their first login with nothing to sync; binding through the trusted admin API keeps MT.1's "never by a token's email" rule |
| Nov 21 | MT.4 | Adding someone records one event, `member.add` (or `member.owner_assigned_by_platform`), with `invited` in its changes; no separate `user.create` | One request, one event (the audit guard's rule); the event says a new person was invited |
| Nov 21 | MT.4 | Changing your own role is `409`; removing yourself through `DELETE /members/{user_id}` is allowed (as `member.remove`), under the last-owner rule like leaving | Decision 9 forbids only your own role change; the last-owner rule protects the rest |
| Nov 21 | MT.4 | Disabling a user is not refused when they are a workspace's last active Owner | A compromised account must be stoppable at once; the Platform Admin is the safety net and can assign a new Owner |
| Nov 21 | MT.4 | e2e invites a new address every run and deletes its membership, GoTrue account and email afterwards; its `users` row stays | The invitation is only really sent to someone new; people are never deleted, only disabled |
| Nov 21 | MT.5 | A transition from the wrong status answers `409 workspace_<status>` naming the current one; `default` answers `409 default_workspace`; a restore past `purge_after` `409 restore_period_over` | One shape for every refused transition, and the dashboard learns where the workspace is without another call |
| Nov 21 | MT.5 | A suspended workspace is read-only for members, but platform power (Platform Admin with MFA, audited as such) still writes there, deleting it included | Decision 10 says "read-only for members"; the Platform Admin must be able to clean up or delete an abusive workspace without first letting its traffic flow again |
| Nov 21 | MT.5 | A deleted or purged workspace answers 404 on every workspace endpoint for everyone, the Platform Admin included; leaving one is 404 too | Its data is frozen until restore or purge; the platform list (`?status=`) and restore are the Platform Admin's way in |
| Nov 21 | MT.5 | Restore always returns to `active`, also when the workspace was suspended at deletion | Only the Platform Admin can delete a suspended workspace and only they restore; remembering the old status would need another column for a rare case |
| Nov 21 | MT.5 | Restore is refused after `purge_after` even if the purge job hasn't run yet | "Within 30 days" as promised, and no race with a purge that may be running; both lock the row (the job skips locked rows) |
| Nov 21 | MT.5 | `PATCH /limits` takes `null` to put a limit back to the platform default (other PATCHes refuse null); bounds 1–1,000,000 requests/min, 1–10,000 in flight, 1–10,000 services, 1–100,000 routes, 1–1,000,000 consumers | NULL is the column's meaning (decision 6); the bounds stop a typo meaning "no limit" |
| Nov 21 | MT.5 | Quotas lock the workspace row `FOR NO KEY UPDATE` before counting; lowering a quota below current use only refuses new objects | Parallel creations count one after the other (tested with 10 at once); the lock doesn't block other writes' foreign-key checks (`KEY SHARE`) |
| Nov 21 | MT.5 | Workspace answers carry `limits` (effective) and `limit_overrides` (own); `GET /workspaces/{slug}` adds `usage` | The Settings and Platform pages show both the value in force and whether it is a default, and how full each quota is |
| Nov 21 | MT.5 | `PATCH /api/v1/workspaces/{slug}` (rename, `workspace:write`, `workspace.update`) built in MT.5 | PLAN.md lists it, no step had it, and `workspace:write` had no endpoint |
| Nov 21 | MT.5 | Renames and limit changes are recorded in the workspace's own log; lifecycle events stay platform-level | Owners should see why their quota moved; lifecycle events must survive the purge of that log |
| Nov 21 | MT.5 | The purge job has its own login (`apim_purge`) and container (`workspace-purge`), like retention; it deletes children first, keeps the row as `purged`, and records `workspace.purged` (actor `system`) with how many rows of each table went | The API's login can't delete audit events, and must not; a guard test checks the job's table list against every model with a `workspace_id` |
| Nov 21 | MT.5 | The two database jobs share `app/core/jobs.py` (asyncpg URL, the daily loop) | One loop, one failure rule for both |
| Nov 21 | MT.5 | e2e makes a new workspace per lifecycle story (`e2e-lc-<random>`) and leaves it deleted | Slugs are never reused, so a story that deletes must use a new one; the purge job removes it after 30 days |
| Nov 21 | MT.5 | Workspace writes lock the row (`FOR NO KEY UPDATE`) and check status and membership again, after a first unlocked check that answers non-members `404` without the lock. Writes in one workspace therefore run one at a time, including a member add's GoTrue call. Leaving a suspended workspace returns `403 workspace_suspended`. `PATCH /limits` returns `404` for deleted or purged workspaces (after review) | A member write and a lifecycle transition or role change run in order. Strangers can't stall a workspace or learn from timing that it exists. Admin writes are rare, so one at a time per workspace costs nothing noticeable. Both endpoints honor the workspace state rules |
| Nov 21 | MT.5 | Adding a new person whose GoTrue id our `users` already holds (a parallel add, or an account whose email changed in GoTrue) is `409`, not `500` (after review) | Postgres may report the GoTrue-id index before the email index; the parallel-add test hit it once the timing changed |
| Nov 22 | MT.6 | The config columns are listed per table; only their changes bump. `workspaces` bumps for `slug`, `status`, `rate_limit_per_min`, `max_concurrent` (not the quotas or the name) | The answer carries only those; a test makes every other column be marked "not config" on purpose |
| Nov 22 | MT.6 | `bump_workspaces` is `SECURITY DEFINER` (with a fixed `search_path`), `EXECUTE` for `apim_app` and `apim_purge` only | Writers need no rights on `config_seq` / `workspace_config`; the purge login lost its `config_version` grant |
| Nov 22 | MT.6 | `workspaces` has insert and update triggers, no delete trigger | Workspace rows are never deleted (the config row RESTRICTs it) |
| Nov 22 | MT.6 | The purge keeps `workspace_config` (PLAN.md listed it among the purged tables) | Its newer `seq` puts the purged workspace in `deleted` for a gateway that was away for 30 days; without the row it would keep serving it |
| Nov 22 | MT.6 | `config_seq` starts at the old `config_version` | A gateway running during the upgrade isn't treated as coming from the future |
| Nov 22 | MT.6 | Until MT.10 each service has one target (its `upstream_url`, id = the service id, weight 100); the gateway resolves a route's upstream and timeout from its service at parse time | The answer has PLAN.md's final shape now, so MT.10 only adds targets; the proxy is unchanged |
| Nov 22 | MT.6 | The answer carries every workspace; the gateway serves only `default` until MT.7 (no `default` → `404 route_not_found`) | Replaces the MT.2 rule "only `default` in the answer": the isolation now lives in the gateway, where MT.7 extends it to every Host |
| Nov 22 | MT.6 | The per-`(workspace, version)` cache keeps one entry per workspace (its latest version) and drops deleted ones; one answer is built at a time | Bounded by the number of workspaces; a change waking many gateways builds each workspace once |
| Nov 22 | MT.6 | A protocol error (a partial answer before any full one, or behind the current `seq`) keeps the snapshot and asks `since=0` after the usual backoff | Never guess; the backoff stops a misbehaving control plane from causing a busy loop |
| Nov 22 | MT.6 | Review fixes add `X-Config-Generation`, make config tables read-only to the API role, and stream the existing JSON envelope by workspace | A restore can reuse sequence numbers, runtime writes belong in the triggers, and full sync must handle configs larger than 64 MiB |
| Nov 22 | MT.6 | A workspace the gateway can't read is dropped (not served) and the rest of the answer applies; after a partial answer the gateway asks `since=0`. An answer is still refused whole when it is unreadable, incomplete, has a workspace without an id, or is over 1 GiB (second review) | Keeping its old config would leave stale keys in service; refusing the whole answer froze every tenant on one bad workspace, and a new gateway couldn't load any config. The cap was removed with the 64 MiB limit; 1 GiB keeps large configs working while a broken answer can't exhaust memory |
| Nov 22 | MT.7 | Only `<one label>.GATEWAY_BASE_DOMAIN` names a workspace; every other name (the bare domain, `localhost`, IPs, `gateway:8080`, no Host) serves `default`. Under the base domain, anything but one valid label is `404 workspace_not_found` | PLAN.md said "unknown host → 404"; internal clients (e2e, k6, Docker) reach the gateway by service name. Like a reverse proxy's default server: such names can never lead to another tenant |
| Nov 22 | MT.7 | `GATEWAY_BASE_DOMAIN` is optional, default `localhost`, and checked as a domain name at start | `*.localhost` needs no DNS, so the default works locally; a typo stops the gateway instead of making every workspace unreachable |
| Nov 22 | MT.7 | The `tenant` step reads the snapshot once (and answers `503 config_not_loaded`); the router matches in the workspace from `reqctx` and no longer reads the snapshot | One config version per request, as before; the workspace travels in `reqctx` (AGENTS.md) instead of packages importing each other |
| Nov 22 | MT.7 | The control plane writes JSON lines: one `request` line per call (uvicorn's access log off) and `request_id` / `workspace_id` on every app line, through per-request notes in a context variable; `httpx` at WARNING | "workspace_id on every control-plane log line" needed structured logs; httpx at INFO would log GoTrue lookups by email |
| Nov 22 | MT.8 | A refused answer describes the bucket that holds the request back longest: `Retry-After` is the longest wait, `X-RateLimit-Limit` / `-Remaining` are that bucket's, and on a tie the consumer's own limit is named. Allowed answers describe the consumer's bucket (the workspace's on a public route) | PLAN.md said "remaining of consumer or workspace" without saying which. A retry needs a token from both, so an earlier one would only be refused again; the consumer's own limit is the one its developer can act on |
| Nov 22 | MT.8 | The script is now `ratelimit/buckets.lua` (was `tokenbucket.lua`); with one key it behaves exactly as before | It holds two buckets; the name says so |
| Nov 22 | MT.8 | A workspace whose limits are missing or below 1 is dropped from the config, like one with an unknown status | Without them the gateway couldn't protect the others from it (a limit of 0 would also refuse all its traffic, or fail open on every request). The control plane always sends both (CHECKs) |
| Nov 22 | MT.8 | In-flight counting lives in its own `inflight` package (PLAN.md section 9 named a `concurrency` middleware): one atomic counter per workspace id, taken with compare-and-swap, never removed; the limit comes with each request from the snapshot | Counters must outlive the config (a workspace's config is replaced on every change, its running requests are not), and a changed limit applies at once. Compare-and-swap never lets a refused attempt make a free slot look taken. An entry is a few bytes per workspace served, and workspace ids are never reused |
| Nov 22 | MT.8 | The in-flight step is the last before the proxy; requests refused earlier (404, 401, 403, 429) never take a slot. No new log field: the 503 line carries the route and workspace | Only requests on their way to a backend tie up the instance |
| Nov 22 | MT.8 | The rate-limit and in-flight steps answer 500 when no workspace is on the request (only possible if `main` wires them without `tenant`) | Like the router and auth: a wiring mistake must not let traffic through unlimited |
| Nov 22 | MT.8 | The k6 load test logs in with password and TOTP (its own factor `loadtest`, removed afterwards; the runner gets `GOTRUE_SERVICE_ROLE_KEY` like e2e), raises `default`'s rate limit to 1,000,000/min for the run and puts back the override that was there (also when setup fails) | `default`'s 6,000/min would refuse most of 300 requests/s; `PATCH /limits` is a Platform Admin endpoint and needs MFA; raising `WORKSPACE_RATE_LIMIT_PER_MIN` would change the default for every workspace |
| Nov 22 | MT.9 | The deny list adds `::` (unspecified), `240.0.0.0/4` (reserved and broadcast) and judges IPv6 addresses that carry an IPv4 one (NAT64, 6to4, IPv4-compatible) by that IPv4 address | Each is another spelling of an internal address: `64:ff9b::a00:1` reaches 10.0.0.1 through a NAT64 gateway, `::ffff:` was already in the plan |
| Nov 22 | MT.9 | An allowed host name opens its addresses only when the upstream URL uses that name; the same address as a literal or under another name stays refused | The dialer's hook sees only the address; checking the name before dialing keeps "allowed together with the addresses it resolves to" without opening Docker's whole network to every tenant |
| Nov 22 | MT.9 | The control plane's known internal names are `localhost`, `*.localhost` and `*.internal`; single-label names such as `redis` pass on save | `*.localhost` always means this machine (RFC 6761). A single label may well be a tenant's real service name; the gateway refuses it at connect time if it resolves inside |
| Nov 22 | MT.9 | The early check runs in the service schema's validator (422 in pydantic's error shape), after URL normalization | Same place and shape as the scheme and credentials rules; `http://2130706433` and `http://0x7f.0.0.1` reach it as `127.0.0.1` |
| Nov 22 | MT.9 | Compose defaults `UPSTREAM_ALLOW` to `mock-backend` with `${UPSTREAM_ALLOW-mock-backend}` (no colon), so an existing `.env` without the line keeps the local stack working and an explicit empty value allows nothing | Upgrading developers need no `.env` change; production sets it empty or to its own hosts |
| Nov 22 | MT.9 | A policy refusal is never reported as a timeout, even when the route's time ran out meanwhile; no new log field on the request line, a separate warning names host and address | The client and the operator see the real cause; the request line's status 502 and the warning are enough |
| Nov 22 | MT.9 | Review fixes: the SSRF warning is throttled per workspace and host (10 s, with `skipped_warnings`); `UPSTREAM_ALLOW` refuses entries that could never match (a zoned address, an IPv4-mapped network shorter than /96) and accepts `_` in host names; a mixed answer whose allowed address fails is a connection error; a client that hung up stays `499`; the rebinding test sets its answers between dials; fuzz tests for the allow-list parser (Go, also in CI) and seeded random checks in the control plane | A tenant could write one warning per request; an exception that looks set must work; Docker serves names with `_`; the status must name the real cause; AGENTS.md wants fuzz tests for security parsers. The control plane has no fuzzing library, and a seeded random test needs no new dependency |
| Nov 22 | MT.10 | The config sequence moves once per **transaction**, not per statement (`bump_workspaces` remembers the transaction's sequence in a transaction-local setting); each workspace's version moves once | Creating a service with its targets is two statements and was two bumps, two NOTIFYs and two gateway wake-ups for one change; the same for cascades and the purge |
| Nov 22 | MT.10 | Migrated targets keep the service's id as their id | The gateway already used that id (MT.6), so a running gateway's balancer and log ids stay the same through the upgrade |
| Nov 22 | MT.10 | At most 50 targets per service (`409`); the last target can't be removed (`409`, switch it off instead); a duplicate URL in one service is `409` (`422` within one create request); a target's `PATCH` changes only `weight` and `is_active` | Each target is health-checked by every gateway instance, so the number is bounded like other quotas; "a service has one or more targets" (PLAN.md section 7) holds; the plan lists "update weight or state". Adding and removing targets lock the service row, tested with parallel deletes |
| Nov 22 | MT.10 | Health-setting bounds: interval 1–300 s, thresholds 1–10, passive failures 1–100, eject 1–3600 s; `hc_path` a path only; `lb_algorithm` only `round_robin` | Decided here (the plan had defaults only); a CHECK for each in the database |
| Nov 22 | MT.10 | Targets made in one request get their creation times in the order sent | Postgres's `now()` is the same for a whole transaction, so "oldest first" would otherwise follow the random ids |
| Nov 22 | MT.10 | The gateway keeps a service's balancer while its fingerprint (targets' ids, URLs, weights in order, health settings) is the same; services of suspended workspaces aren't checked; a request on an older config gets a temporary balancer the registry doesn't keep | Health state survives unrelated config changes (decision 14); checkers never outlive their service |
| Nov 22 | MT.10 | Passive checks count connection errors, timeouts and 5xx; client hang-ups and egress refusals count for nothing, for active checks too | A refused target must keep answering `502 upstream_not_allowed` (MT.9), not hide behind `503 no_healthy_upstream` |
| Nov 22 | MT.10 | One active check times out at the interval, at most 5 s; a 3xx is healthy and not followed; each target's first check waits a random part of the interval | "A short timeout" made concrete; checks of many targets (and a restarted gateway's) don't fire at once |
| Nov 22 | MT.10 | The mock backend names its instance in the echo and can be made to act down (`PUT /_mock/state`); Compose runs `mock-backend-2`, and `UPSTREAM_ALLOW` defaults to both | e2e stops a target without Docker access; it runs only in the test stack |
| Nov 22 | MT.11 | The API shows a route's CORS settings as one `cors` object (stored in five columns); `PATCH` replaces it as a whole; origins are normalized as browsers send them; `*` stands alone and never with credentials | One setting the dashboard edits in one place; a browser compares origins exactly, so `HTTPS://App.example.com:443/` must be stored as `https://app.example.com`; browsers refuse `*` on a credentialed call |
| Nov 22 | MT.11 | `max_body_bytes: null` in a `PATCH` puts the route back on the platform default (`PartialUpdate` lets a schema name such fields); the config answer carries the effective value | The same meaning of null as `PATCH /limits`; the gateway never needs the default |
| Nov 22 | MT.11 | `MAX_REQUEST_BODY` and `MAX_REQUEST_BODY_CEILING` take bytes or a size with a unit (`10MiB` binary, `10MB` decimal; pydantic's `ByteSize`); defaults 10 MiB and 100 MiB; a default above the ceiling stops the start | "10MB" in the plan; binary units keep the plan's example value (10,485,760) |
| Nov 22 | MT.11 | A preflight is matched by path only and answered `204` even when refused (no allow headers); a plain `OPTIONS` without `Origin` or a requested method stays an ordinary request (`405` if the route doesn't allow it) | The plan's rule; the browser decides from the headers, and other OPTIONS uses keep their behavior |
| Nov 22 | MT.11 | On a route with CORS on, the gateway replaces a backend's own `Access-Control-Allow-Origin` and `-Credentials`, and sets them on the gateway's own errors too | Two such headers make a browser reject the answer; a page must be able to read a 401 or 429 |
| Nov 22 | MT.11 | Order after the router: CORS, then the body limit, then auth | A preflight carries no key; a too-large body is refused before it costs a rate-limit token or a slot |
| Nov 22 | MT.11 | A body cut at the limit while uploaded is the client's `413`, not a target failure, and the log gets `body_limit` | It says nothing about the backend; the decision said the limit shows in the log line |
| Nov 22 | MT.12 | A changed platform default (`WORKSPACE_RATE_LIMIT_PER_MIN`, `WORKSPACE_MAX_CONCURRENT`, `MAX_REQUEST_BODY`) reaches running gateways: `config_seq.defaults` records the defaults the answers were built with; at startup `apply_config_defaults(...)` (`SECURITY DEFINER`, `EXECUTE` for `apim_app` only) bumps the workspaces that inherit a changed one and stores the new values, under the `config_seq` row lock. An unknown record (the first start after the upgrade) counts as every default changed | Without it, a gateway asking `since=N` kept the old value until that workspace changed for another reason, while a newly started gateway got the new one: two instances enforcing different limits. Only inheriting workspaces move, so other gateways' caches stay warm. All control-plane instances must share the defaults (otherwise each start bumps again) |
| Nov 22 | MT.12 | The quota defaults (`WORKSPACE_MAX_SERVICES` and the others) and `MAX_REQUEST_BODY_CEILING` are not recorded | The gateway never sees them; the control plane reads them on every write |
| Nov 22 | MT.12 | The upstream health tests count checks at the client's transport (requests started), not at the test server | `stop()` waits for the checkers, so no check can start after it returns, but a check written just before can still arrive afterwards: counting at the server was flaky |
| Nov 22 | MT.12 | The MT definition of done is one e2e module with a module-scoped setup (`test_mt_done.py`) and two fixed Owner accounts reused by every run; changes are matched in the audit log by their `X-Request-ID` | The definition is one connected story; request ids prove each change is in its own log and not the other's without relying on target ids or timing |
| Nov 22 | MT.12 | The k6 test runs in its own workspace `lt-<run id>` (made by the Platform Admin with MFA, rate limit raised, deleted at the end) instead of raising `default`'s limit; the direct baseline alternates between the two mock backend instances | Host-based routing and two targets as the step asks, and the test no longer changes a workspace someone else uses. k6 runs init code once per VU, so the run id, workspace and host travel in setup's data |
| Nov 22 | Plan | The backend is finished in 5 large steps (V2.1–V2.3, P.1–P.2); the frontend follows | An AI agent builds each step whole; fewer, larger steps with clear done criteria instead of many small ones |
| Nov 22 | Plan | Essential: usage events, request logs and analytics, plans and monthly quotas, metrics, the developer portal with the API lifecycle; plus per-IP limits on public routes and limits on failed key attempts | The test: could an API management platform be shown or sold without it? The two protections close gaps left open since 4.2 and 3.6, and open portal sign-up makes them more urgent |
| Nov 22 | Plan | Out of plan (kept in PLAN.md, marked, not deleted): JWT auth, transforms, caching, circuit breaker, LLM gateway mode, webhooks, tracing, Grafana, deployment to a server, workspace tiers | Not needed for a complete platform; the plan stays focused |
| Nov 22 | Plan | Usage events go over a Redis Stream (`XADD` with a cap) to a `usage-ingest` worker (consumer group, ack after commit, `event_id` for idempotency), not HTTP batches with an Inbox | The gateway only appends and moves on, a control-plane outage loses nothing, and Redis is already there. Tyk works the same way (gateway → Redis → Tyk Pump) |
| Nov 22 | Plan | Limits come from plans that a workspace's Owner or API Admin defines; the workspace ceiling stays the Platform Admin's; a plan above the workspace's limit is `422`; no per-consumer override | Checked against the vendors' docs: AWS (account limits set by AWS, usage plans by the API owner, per-client limits never above the account's), Apigee (API products with quotas), Kong (consumer groups as tiers), Tyk (policies override a key's own settings). A special customer gets its own plan |
| Nov 22 | Plan | The platform runs locally: the deployment step was dropped; only backup and restore moved into P.2 | The demo is local; a tested restore is still worth having |
| Nov 22 | Plan | Developers get their own GoTrue (`auth-portal`) and table (`developers`); a developer app is backed by a consumer | Open sign-up must never touch workspace members' accounts (decision 8's v3 note); reusing consumers keeps the gateway, plans, quotas and keys unchanged |
| Nov 23 | V2.1 | Every request whose workspace is known gets a usage event, refused ones (401, 404 inside a workspace, 429) included; only requests refused before a workspace was found (unknown host) are in the gateway's log alone | Errors and refusals are what an API owner most needs to see; "proxied request" read narrowly would hide them |
| Nov 23 | V2.1 | Usage events get their own Redis client (2 s timeouts) from a new `redisconn` package; the stream is capped at about 200,000 entries (`StreamMaxLen`, a constant); batches of at most 100 every 200 ms | A batch must never hold up a 50 ms rate-limit check. 200,000 entries are some 100 MB and 11 minutes of traffic at 300 requests/s while the worker is away. Small batches keep Redis, which runs one command at a time, from working through a long pipeline while rate-limit checks wait |
| Nov 23 | V2.1 | Event ids are UUIDv7 (time first), made by the gateway; the worker deletes stored entries from the stream (`XACK`, then `XDEL`) | Ids made later sort later, so the request log's index grows at its end. Deleting keeps the stream small in normal operation, so the cap only bites while the worker is away |
| Nov 23 | V2.1 | Partitions are UTC months, made and dropped by two `SECURITY DEFINER` functions (`ensure_request_log_partitions`, `drop_request_log_partitions`) only `apim_ingest` may call; the worker makes a month on first use and drops expired ones daily; a new partition gets no privileges of its own | Only the table's owner may make or drop partitions, and the API's login must never get that power. Revoking the partition's own grants keeps the API from writing a partition directly (the schema's default privileges would allow it) |
| Nov 23 | V2.1 | The worker drops (acks and counts) malformed entries and those outside [now − retention, now + 1 day] | A poison entry must not block the stream, and a broken clock must not make partitions for years ahead |
| Nov 23 | V2.1 | `set_updated_at` ignores `last_used_at`; `apim_ingest` gets `SELECT (id, last_used_at)` and `UPDATE (last_used_at)` on `api_keys` | A key being used isn't an edit of the key: `updated_at` keeps showing the last admin change. The UPDATE's `WHERE` reads those two columns |
| Nov 23 | V2.1 | Analytics: `from`/`to` (default the last day, at most 31 days), buckets of a minute, hour or day aligned to UTC with empty ones as zero, at most 1,500 buckets; 4xx and 5xx counted apart; p50, p95 and p99 of `latency_ms`; the busiest routes and consumers with their current names (`null`: no route or consumer, or deleted since) | Concrete shapes for the plan's "requests, errors and latency over time, by route and by consumer", bounded so one query stays cheap |
| Nov 23 | V2.1 | A `route_id` or `consumer_id` filter on the analytics or the request log that isn't in this workspace is `422`, as for every filter; the isolation guard checks each filter of an endpoint | One rule everywhere. A deleted route or consumer therefore can't be filtered on; its rows still show unfiltered |
| Nov 23 | V2.1 | The worker imports nothing that needs the API's settings; a test imports it with only its own environment | It crashed at start in Compose because a model import built the API's engine; the guard keeps that from coming back |
| Nov 23 | V2.1 | New dependency: `redis` (redis-py, asyncio) for the worker | The official Python client; nothing else in the control plane reads Redis |
| Nov 23 | V2.2 | Plan names are display text (1–100 characters, trimmed), not slugs | Developers will see them in the portal ("Free", "Pro", "Legacy 60/min") |
| Nov 23 | V2.2 | Creating a workspace adds its `Default` plan in the same transaction, without an event of its own | One request, one event (the audit guard's rule); `workspace.created` covers it |
| Nov 23 | V2.2 | The migration keeps every consumer's `updated_at` (the trigger is off for that one statement) | Moving a consumer onto its plan isn't an edit |
| Nov 23 | V2.2 | One Lua script (`limits.lua`, replacing `buckets.lua`) takes a list of checks, each with a kind and a limit; a refusal names the check that holds the request back longest (the quota waits until the month ends, a blocked IP until its window ends) | Every limit stays one atomic round trip, and one place decides |
| Nov 23 | V2.2 | Wrong keys: a fixed 5-minute window per workspace and IP (`AUTH_FAIL_LIMIT_PER_IP`, default 20). A wrong key costs one small extra call that counts it (401, or 429 once past the limit); a valid key from a blocked IP is refused in the rate-limit call. Missing keys aren't counted; Redis down: 401 as before | "Refused before the lookup" without a second round trip for good requests. A missing key isn't guessing |
| Nov 23 | V2.2 | `PUBLIC_RATE_LIMIT_PER_IP_PER_MIN` (default 600) and `AUTH_FAIL_LIMIT_PER_IP` are platform settings with no per-workspace value; they reach running gateways through `apply_config_defaults` | The plan's "effective values with the workspace". **Worth a decision:** a workspace whose public clients sit behind one NAT, or a load test from one machine, needs the platform setting raised |
| Nov 23 | V2.2 | An allowed answer's `X-RateLimit-*` describe the consumer's bucket on protected routes and, on public routes, whichever of the workspace's and the IP's has fewer tokens left; `X-Quota-Limit` / `-Remaining` come with every answer that has a quota; a `quota_exceeded` or `too_many_failed_attempts` refusal has no `X-RateLimit-*` | A client must never be told it has more left than it does |
| Nov 23 | V2.2 | The client IP is the connection's address (no `X-Forwarded-For`) | Unchanged since 3.5; behind a load balancer every client would share one IP, so trusted proxies come with a deployment |
| Nov 23 | V2.2 | The e2e runner deletes the `authfail:*` counters at its start (over Redis's protocol, no library) | All e2e requests come from one IP; two runs within 5 minutes added up past the limit in `default` |
| Nov 23 | V2.2 | Alembic ignores the request log's month partitions | Autogenerate wanted to drop them |
| Nov 23 | V2.3 | A workspace may set its own `ip_rate_limit_per_min` (`PATCH /limits`, Platform Admin, 1–1,000,000; NULL = `PUBLIC_RATE_LIMIT_PER_IP_PER_MIN`); the failed-key limit stays platform-wide | V2.2's open decision, decided by the owner: clients behind one NAT, or a load test from one machine, no longer need the platform-wide value raised |
| Nov 23 | V2.3 | Gateway metrics come from the usage event the logger builds (requests, latency, refusals); in-flight, target health, the config's sequence and last sync, and usage events written and dropped are read at scrape time; a registry of its own (no global state) | No extra work on the request path beyond a few counter updates |
| Nov 23 | V2.3 | `METRICS_ADDR` (default `:9091`) is never published to the host; an empty value (settings built by hand in tests) serves nothing | Metrics show which workspaces exist and how busy they are |
| Nov 23 | V2.3 | The usage-ingest worker serves its metrics on `INGEST_METRICS_PORT` (default 9092): rows stored, repeats, drops, batches and the stream's length | "Lag" made concrete: the worker deletes what it stored, so the stream's length is what waits |
| Nov 23 | V2.3 | New dependencies: `github.com/prometheus/client_golang` (gateway), `prometheus-client` (worker); `prom/prometheus:v2.55.1` in Compose (UI on 127.0.0.1:9090) | The official clients; the plan named Prometheus |
| Nov 23 | P.1 | The developers' GoTrue puts its own audience on its tokens (`developer`, `GOTRUE_JWT_AUD`) besides its own secret, and the control plane refuses to start when `PORTAL_GOTRUE_JWT_SECRET` equals `GOTRUE_JWT_SECRET` | Two independent locks: a copied setting can't make one GoTrue's tokens pass as the other's |
| Nov 23 | P.1 | The control plane gets only `PORTAL_GOTRUE_JWT_SECRET`, not the planned `PORTAL_GOTRUE_URL`; the portal GoTrue's admin key (`PORTAL_GOTRUE_SERVICE_ROLE_KEY`) goes to the e2e runner only | The control plane never calls that GoTrue (it only verifies its tokens), so a URL would be an unused setting and the key an unused secret |
| Nov 23 | P.1 | `actor_type` gains `developer` in P.1 (planned for P.2): a developer's first portal request records `developer.create`, a platform-level event by the developer themself (`actor_user_id` is their `developers` row) | Open sign-up is worth seeing in the audit log, like `user.create` for members |
| Nov 23 | P.1 | Added: `GET /api/v1/developers` and `PATCH /api/v1/developers/{id}` `{status}` (Platform Admin; `developer.block`, `developer.unblock`); a blocked developer gets `403 developer_blocked` on every portal request | The plan had a `status` column with no way to set it; open sign-up needs a way to stop an abusive developer. P.2 also blocks their apps' consumers |
| Nov 23 | P.1 | Added: `GET /api/v1/portal/me` and `GET /api/v1/portal/workspaces` (active workspaces with at least one published or deprecated API, with the count) | The portal pages (FE.4) need the developer's own account and a way to find catalogs without knowing slugs |
| Nov 23 | P.1 | Only active workspaces have a catalog: suspended, deleted and unknown ones are the same `404 Workspace not found` | A suspended workspace serves no traffic, so showing its APIs would invite apps that can't work |
| Nov 23 | P.1 | API lifecycle: `draft` → `published` → `deprecated`, plus `deprecated` → `published`; other moves `409 api_<status>` (as the workspace lifecycle); the slug is immutable; a new API is always a draft | Developers may rely on a published API, so it never goes back to a hidden draft; undoing a deprecation is harmless |
| Nov 23 | P.1 | `api_routes` is keyed by `route_id` (no own id or `updated_at`); deleting a route or an API removes only the link (CASCADE); APIs bump no config | One route in one API at most, by the primary key; the gateway serves routes the same whether an API includes them or not |
| Nov 23 | P.1 | OpenAPI documents: an object with `"openapi": "3.x"` or `"swagger": "2.0"` and an `info` object, at most 1 MiB as compact JSON; a database CHECK at 2 MiB of Postgres's own printing as the safety net; audit events keep a fingerprint (`sha256:` + 16 hex), never the document; lists leave documents out (`has_openapi`) | No validator dependency: a broken document only hurts its own docs page. A megabyte per event would bloat the append-only log |
| Nov 23 | P.1 | Code in three feature folders (`apis`, `developers`, `portal`) instead of the one `portal/` folder of PLAN.md's repo tree | One folder per feature (AGENTS.md); members' API endpoints and developers' endpoints stay apart |
| Nov 23 | P.1 | The JSONB column `apis.openapi` uses `none_as_null`, so "no document" is SQL NULL, not the JSON value `null` | Found by the database CHECK in the first test run: SQLAlchemy stores `None` as JSON `null` by default |
| Nov 23 | P.1 | The rate-limit burst tests allow 250 ms between the test's clock and Redis's (`redisClockSlack`) | The script refills by Redis's clock; Docker Desktop steps its VM's clock to the host's now and then, which failed the exact refill check about once in 20 runs (an open item since V2.2) |
| Nov 23 | P.2 | `apps` gains `api_id` (the API the app was made for; RESTRICT, so an API with apps is `409 api_in_use`); an app's keys still reach every protected route of its workspace | "Deprecated APIs take no new apps" needs to know an app's API. Keys limited to an API's routes would change the gateway (Apigee's API products); the plan keeps the gateway unchanged, so that is left for later |
| Nov 23 | P.2 | An app's consumer is `app-<app id>` with the developer's email, on the chosen plan; it is active exactly while the app is approved and the developer isn't blocked (`consumer_status`). Deleting the consumer (members' API) deletes the app (CASCADE) | One rule decides whether an app's keys work; the gateway reads only the consumer, as before. A unique, stable name never clashes with members' consumers |
| Nov 23 | P.2 | Members decide with `PATCH .../apps/{id} {status: approved \| rejected}`, also later (rejecting an approved app takes its access away; approving again gives it back); `pending` can't be set; no separate "block" for apps | One field, two decisions: "block" is a rejection. A consumer can still be blocked through the consumers API, as an admin's override |
| Nov 23 | P.2 | A developer has at most 10 apps per workspace (`409 quota_exceeded`), and apps count against the workspace's consumers quota | Open sign-up must not let one developer use up a workspace's consumers |
| Nov 23 | P.2 | Keys only for approved apps (`409 app_pending`, `409 app_rejected`); otherwise the admin API's key rules (at most 10 active, shown once, revoke idempotent); a key of another app is `404` | A key that can't work yet would only confuse |
| Nov 23 | P.2 | Developers' writes in a suspended workspace are `403 workspace_suspended`; their apps stay readable. Apps in deleted workspaces are hidden (`404`) | The members' rules (decision 10), applied to developers |
| Nov 23 | P.2 | Added: `GET /api/v1/portal/workspaces/{slug}/plans` (the `portal_visible` plans) and `DELETE /api/v1/portal/apps/{id}` (with its consumer and keys) | A developer must see the plans to choose one, and must be able to remove an app (else the 10-app cap could lock them in) |
| Nov 23 | P.2 | The app's usage: the analytics' traffic for its consumer, plus `quota` (`used` = this UTC month's request-log rows with `ratelimit` = `ok`, `remaining`, `resets_at`) | The control plane never reads Redis; the request log is close behind the gateway's counter (seconds), and the answer says so |
| Nov 23 | P.2 | Blocking a developer blocks the consumers of their approved apps in every workspace in the same transaction (one `developer.block` event); unblocking reactivates them | A blocked developer's keys must stop at the gateway, not only the portal |
| Nov 23 | P.2 | Backup: `scripts/backup.ps1` dumps `apim`, `auth` and `auth_portal` (custom format, copied out of the container); restore: `scripts/restore.ps1` into a fresh stack only (it refuses a database with tables), then a new config generation, migrations and the stack. PowerShell only, like the owner's tooling; no `.sh` twins | A restore must never overwrite data by mistake; the GoTrue databases are part of the backup so people and developers can still log in. Windows PowerShell 5.1 mangles binary pipes and quotes in native arguments, so the dumps are copied with `docker compose cp` and SQL goes in on stdin |
| Nov 23 | FE.1 | Stack versions: React 19.3, Vite 7.3, TypeScript 5.9, Tailwind 4.3, shadcn/ui (generated with its CLI, kept as generated), TanStack Query 5, React Router 7, react-hook-form with zod 3, Vitest 3, MSW 2; newer majors exist (Vite 8, Vitest 5, React Router 8) but these are the lines the builder knows well. New dependencies beyond PLAN.md section 2: `@supabase/auth-js` (only the GoTrue client of supabase-js), `openapi-fetch` and `openapi-typescript` (typed calls from openapi.json), `sonner` (toasts), `date-fns` | Fewer surprises in a big step; every version is pinned exactly in package.json and can be raised later on purpose |
| Nov 23 | FE.1 | Vite also forwards `/auth/v1` and `/portal-auth/v1` to the two GoTrues (not only `/api`) | The browser then never calls another origin, so neither GoTrue needs CORS settings |
| Nov 23 | FE.1 | The app reads GoTrue's email links itself (the URL fragment) instead of letting the client do it, and removes the tokens from the address bar | It must know whether a link was an invitation or a recovery to ask for a password first |
| Nov 23 | FE.1 | A person with several verified authenticators picks one at the MFA step | The first one only would lock out anyone whose other app is the one at hand (the e2e stories hold two for the Platform Admin) |
| Nov 23 | FE.1 | API types are generated with `--default-non-nullable false` | A request field with a server default is optional, as the API says; the default made every create body repeat all defaults |
| Nov 23 | FE.1 | Frontend checks run locally with Node (`frontend/scripts/check.ps1`), like the gateway's with Go; tests render the whole app against a fake GoTrue and the API faked with MSW. Coverage gate: lines and statements 90%, functions 85%, branches 80%; the generated shadcn/ui components are left out of coverage and of the strict lint rules | No stack needed for unit tests; the generated components aren't ours to test |
| Nov 23 | FE.1 | Browser stories with Python Playwright in their own container (`e2e-browser`, test profile), reusing the e2e helpers; the plain e2e run skips them | The same fixtures (GoTrue admin, TOTP) as the API stories; Chromium stays out of the e2e image |
| Nov 23 | FE.2 | The dashboard gets two pages PLAN.md didn't list: **APIs** (drafts, routes, the OpenAPI document, publish and deprecate) and **Apps** (the approval queue); the sidebar is Overview, Services, Routes, Consumers, Plans, APIs, Apps, Members, Activity, Logs, Settings | The backend has these features (P.1, P.2) and nothing else could use them: without the APIs page nothing ever reaches the portal |
| Nov 23 | FE.2 | A service's page shows its targets' settings (weight, on/off) and the health-check settings, not live health | The control plane doesn't know a target's health: only each gateway does, in memory. Showing it needs a gateway endpoint or metric, not built |
| Nov 23 | FE.2 | Overview: "active consumers" counts consumers with requests in the period (from the analytics), "recent errors" are the 4xx/5xx among the newest 100 request-log rows | The API has no count of active consumers and no status-range filter on the request log; both come from data the page already reads |
| Nov 23 | FE.2 | Pages load when first opened, and libraries are built into their own chunks (react, radix, auth, charts, vendor) | The first download stays small, no chunk is over 500 kB, and library files stay cached across app changes |
| Nov 23 | FE.2 | A 422's field errors appear under their fields (`lib/forms.ts`); other refusals show above the buttons, in words for the short codes (`quota_exceeded`, `last_owner`, ...) | The server's checks (an internal upstream, an email domain) are the final word; the person sees them where they typed |
| Nov 23 | FE.3 | Added `GET /api/v1/users` (Platform Admin with MFA): everyone, newest first, each with their workspaces (`slug`, `name`, `role`, `status`); `?is_active=` and `?email=` (part of the address, any case, `%` and `_` taken literally) filter | The Users page needs a list and the backend had only `PATCH /users/{id}`; the memberships come in one query per page, not one per person |
| Nov 23 | FE.3 | The Platform pages check the person before any call: not the Platform Admin → a "not for you" page; no authenticator → a page that sends them to set one up (one with an authenticator is asked for the code at sign-in already) | Every platform endpoint answers `403 mfa_required` without `aal2`; a page that fails call by call would only show errors |
| Nov 23 | FE.3 | The limits dialog sends only the limits that changed; an emptied field sends `null` (back to the platform default), whose value is shown as the placeholder | `PATCH /limits` takes `null` as "default"; sending unchanged values would turn defaults into custom values the next time a default moves |
| Nov 23 | FE.3 | The workspace actions hide what the API refuses anyway: no suspend or delete for `default`, no restore except for deleted ones, nothing for purged ones | Fewer dead ends; the API's 409s still show in words if the list is stale |
