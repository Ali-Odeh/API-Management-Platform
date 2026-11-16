# API Management Platform: plan and architecture

Nov 15, 2026 · @Ali

## 1. Overview and goals

The platform is a self-hosted API management system: a Go gateway that every API request passes through, a FastAPI control plane that configures it, and a React dashboard to manage it. It is a learning project modeled on Kong, Tyk and Apigee.

**Learning goals**

- Learn Go by building a real reverse proxy with auth and rate limiting
- Practice distributed-systems basics: Redis counters, config propagation, caching
- Practice system design: data plane vs. control plane, middleware chains
- End with a portfolio project that runs with one `docker compose up`

**MVP definition of done**

An admin registers a service and a route through the admin API, creates a consumer with an API key, and that consumer calls the backend through the gateway. The request is authenticated, rate-limited and logged. Bad keys get 401, and clients over the limit get 429.

**Out of scope for now**

- Billing and payments
- Kubernetes deployment and multi-region setups
- OAuth2 authorization server, mTLS
- Full multi-tenancy (several workspaces, admin roles, per-tenant gateway routing). The data model is **tenant-ready from day one**: every record belongs to a workspace, and the MVP runs with one `default` workspace. The full feature is milestone MT, after the MVP (decided Nov 16, 2026).

**How we work**

| Part | Who builds it | Where |
| --- | --- | --- |
| Gateway (Go) | Ali and Claude together | Claude Code |
| Control plane (FastAPI) | Ali and Claude together | Claude Code |
| Frontend (React) | Claude | Built against the control plane API |

The backend is the learning part, so Ali writes or reviews every piece of it. The frontend is delivered by Claude once the API it depends on exists.

## 2. Tech stack and decisions

| Layer | Choice | Why |
| --- | --- | --- |
| Gateway | Go 1.23+, standard library (`net/http`, `httputil.ReverseProxy`) | Built for network services; no framework hides how the proxy works |
| Control plane | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2 | Fastest way to build CRUD; opens the door to AI features later |
| Database | PostgreSQL 16 | Source of truth for all config |
| Cache and counters | Redis 7 | Atomic rate-limit counters through Lua scripts |
| Frontend | React, TypeScript, Vite, TanStack Query, React Router, shadcn/ui, Tailwind, Recharts | Standard modern stack with typed API calls |
| Local environment | Docker Compose | One command starts everything |
| Load testing | k6 | Scriptable; reports p95 and p99 latency |
| Metrics (v2) | Prometheus and Grafana | Industry standard |

**Key decisions**

- **Two services, two languages.** The gateway is on the hot path and must be fast. The control plane is CRUD and should be quick to change.
- **The gateway never touches Postgres.** It pulls config from the control plane over HTTP and keeps it in memory. If the control plane goes down, the gateway keeps serving with its last config.
- **Polling before push.** The gateway polls for config every 5 seconds in the MVP. A push model (Redis pub/sub) can replace it later.
- **No Go web framework.** The standard library is enough, and it is what teaches the most.

## 3. System architecture

The system splits into a data plane that handles live traffic and a control plane that holds configuration.

&#91;embedded content: system architecture · 7 components\]

Requests travel along the middle row. Configuration is written in the bottom row and pulled up by the gateway, which never talks to Postgres directly.

**Data plane (gateway)**

- Handles every API request, so it must be fast and stay up
- Keeps routes and key hashes in memory; reads Redis only for rate-limit state
- Stateless, so several instances can run behind a load balancer later

**Control plane**

- Owns all configuration in Postgres and exposes the admin API
- Serves one config snapshot to the gateway
- Can be down for a while without affecting live traffic

**Request flow**

1. A client sends `GET /v1/orders/7` with `X-API-Key`.
2. The gateway matches the path to a route. No match returns 404.
3. It hashes the key and looks it up in memory. A bad key returns 401.
4. It runs the token-bucket check in Redis. An empty bucket returns 429.
5. It forwards the request to the route's upstream with a timeout.
6. It returns the upstream response and writes one log line.

**Config propagation**

Every admin change bumps `config_version`. The gateway polls `/internal/config` every 5 seconds with the version it already has. An unchanged version returns 304, and a new one returns the full snapshot, which the gateway swaps in atomically.

## 4. Features by phase

The MVP is the smallest set that makes one request flow end to end. v2 turns it into a product, and v3 adds the stand-out features.

**Gateway (Go)**

| Feature | What it does | Phase |
| --- | --- | --- |
| Reverse proxy | Forwards `/v1/orders/*` to the `orders` backend | MVP |
| Route matching | Longest path prefix plus HTTP method | MVP |
| API key auth | Checks `X-API-Key`, returns 401 on a bad key | MVP |
| Rate limiting | Token bucket per key in Redis, 429 with `Retry-After` | MVP |
| Request logging | One JSON line per request | MVP |
| Config sync | Polls the control plane every 5 seconds | MVP |
| Timeouts and errors | 504 on a slow backend, 502 on a dead one | MVP |
| Health check | `GET /health` | MVP |
| Monthly quotas | Per-plan request limits | v2 |
| Metrics | `/metrics` for Prometheus | v2 |
| Usage events | Ships request records to the control plane | v2 |
| JWT auth | Validates tokens against a JWKS URL | v3 |
| Transforms | Adds or strips headers, rewrites paths | v3 |
| Response caching | Caches GET responses in Redis | v3 |
| Circuit breaker | Stops calling a failing backend | v3 |
| LLM gateway mode | Tracks tokens and cost per key | v3 |

**Control plane (FastAPI)**

| Feature | What it does | Phase |
| --- | --- | --- |
| Workspaces (tenant-ready) | Every record belongs to a workspace; one `default` workspace is seeded | MVP |
| Admin auth | Email and password login, JWT access token | MVP |
| Services CRUD | Name, upstream URL, timeout | MVP |
| Routes CRUD | Path prefix, methods, service, auth flag | MVP |
| Consumers CRUD | Name, email, status, rate limit | MVP |
| API keys | Generate, list, revoke; shown once, stored hashed | MVP |
| Gateway config endpoint | One snapshot of everything the gateway needs | MVP |
| Plans | Rate limit and monthly quota tiers | v2 |
| Usage analytics | Requests, errors and latency by consumer and route | v2 |
| Multi-tenancy | Several workspaces, admin roles, data isolation, per-tenant config | MT |
| Audit log | Who changed what, and when | v3 |
| API lifecycle | Draft, published, deprecated | v3 |
| Developer accounts | Sign-up and self-service keys for the portal | v3 |

## 5. Database design

The MVP needs seven tables in Postgres, all owned by the control plane. Every table has a `uuid` primary key plus `created_at` and `updated_at` timestamps.

**Tenant-ready:** every config table has a `workspace_id` (FK to `workspaces`, not null, indexed). In the MVP only the `default` workspace exists. All queries live in each feature's `crud.py` and filter by the current admin's workspace, so turning on real multi-tenancy (milestone MT) needs no data migration.

```mermaid
erDiagram
  WORKSPACES ||--o{ ADMINS : has
  WORKSPACES ||--o{ SERVICES : owns
  WORKSPACES ||--o{ CONSUMERS : owns
  SERVICES ||--o{ ROUTES : has
  CONSUMERS ||--o{ API_KEYS : owns
  PLANS ||--o{ CONSUMERS : assigned
  CONSUMERS ||--o{ REQUEST_LOGS : makes
  ROUTES ||--o{ REQUEST_LOGS : serves
```

**MVP tables**

| Table | Columns | Notes |
| --- | --- | --- |
| `workspaces` | `name`, `slug` (unique) | One row (`default`) in the MVP |
| `admins` | `workspace_id` (FK), `email` (unique), `password_hash` | Password hashed with argon2. Email is unique across all workspaces so login needs only email and password |
| `services` | `workspace_id` (FK), `name`, `upstream_url`, `timeout_ms` (default 5000), `is_active` | One row per backend. Unique on (`workspace_id`, `name`) |
| `routes` | `workspace_id` (FK), `service_id` (FK), `path_prefix`, `methods` (text array), `strip_prefix` (bool), `auth_required` (bool), `is_active` | Unique on `path_prefix` across all workspaces in the MVP, because the gateway matches by path only. MT makes it unique per workspace once the gateway can tell tenants apart |
| `consumers` | `workspace_id` (FK), `name`, `email`, `status` (`active` or `blocked`), `rate_limit_per_min` (default 60) | The apps that call the APIs |
| `api_keys` | `workspace_id` (FK), `consumer_id` (FK), `key_prefix`, `key_hash` (unique), `name`, `last_used_at`, `revoked_at` | The raw key is never stored |
| `config_version` | `version` (bigint) | Single row, bumped on every config change |

**v2 tables**

| Table | Columns | Notes |
| --- | --- | --- |
| `plans` | `name`, `rate_limit_per_min`, `monthly_quota` | Replaces the per-consumer limit; `consumers` gets `plan_id` |
| `request_logs` | `ts`, `consumer_id`, `route_id`, `method`, `path`, `status`, `latency_ms` | Partition by month; feeds the analytics pages |

**API key format**

A key looks like `ak_live_` followed by 32 random characters. The control plane stores the first 12 characters as `key_prefix` for display and the SHA-256 hash of the full key as `key_hash`. SHA-256 is right here because keys are long and random, so slow password hashes add nothing.

**Redis keys**

| Key | Type | Purpose | Expiry |
| --- | --- | --- | --- |
| `rl:{key_id}` | Hash (`tokens`, `ts`) | Token bucket state per API key | 2 minutes idle |
| `quota:{consumer_id}:{yyyymm}` | Counter | Monthly quota (v2) | 35 days |

## 6. Control plane API

The admin API lives under `/api/v1` and needs an admin JWT on every call except login. The gateway uses one separate internal endpoint protected by a shared secret.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/auth/login` | Returns an access token |
| GET | `/api/v1/auth/me` | Current admin |
| GET, POST | `/api/v1/services` | List, create |
| GET, PATCH, DELETE | `/api/v1/services/{id}` | Read, update, delete |
| GET, POST | `/api/v1/routes` | List, create |
| GET, PATCH, DELETE | `/api/v1/routes/{id}` | Read, update, delete |
| GET, POST | `/api/v1/consumers` | List, create |
| GET, PATCH, DELETE | `/api/v1/consumers/{id}` | Read, update (including block), delete |
| GET, POST | `/api/v1/consumers/{id}/keys` | List keys, generate a key |
| DELETE | `/api/v1/keys/{id}` | Revoke a key |
| GET | `/internal/config` | Config snapshot for the gateway |
| GET | `/health` | Liveness |

**Conventions**

- Lists take `?page=` and `?page_size=` and return `{ items, total }`
- Errors return `{ "detail": "..." }` with the right status code
- FastAPI generates the OpenAPI spec, and the frontend generates its TypeScript types from it

**Generate a key**

```json
POST /api/v1/consumers/{id}/keys
{ "name": "mobile app" }

201 Created
{
  "id": "0b7e...",
  "name": "mobile app",
  "key_prefix": "ak_live_3f9a",
  "key": "ak_live_3f9a8c1d5e7b2a4f6c8d0e1f3a5b7c9d",
  "created_at": "2026-11-15T16:00:00Z"
}
```

The `key` field appears in this response only. Later reads return the prefix.

**Gateway config snapshot**

```json
GET /internal/config
X-Internal-Token: <shared secret>
If-None-Match: "42"

200 OK
ETag: "43"
{
  "version": 43,
  "routes": [
    {
      "id": "r1", "path_prefix": "/v1/orders", "methods": ["GET", "POST"],
      "strip_prefix": false, "auth_required": true,
      "upstream_url": "http://mock-backend:9000", "timeout_ms": 5000
    }
  ],
  "keys": [
    {
      "id": "k1", "key_hash": "9f86d0...", "consumer_id": "c1",
      "consumer_status": "active", "rate_limit_per_min": 60
    }
  ]
}
```

If the version has not changed, the control plane answers `304 Not Modified` with no body.

## 7. Gateway design

The gateway is a chain of middleware around one reverse proxy. Each middleware either rejects the request or passes it on.

**Middleware order**

1. **Recover**: catches panics and returns 500 instead of crashing
2. **Request ID**: reads or creates `X-Request-ID` and passes it upstream
3. **Logger**: records start time, writes the log line when the response is done
4. **Route match**: finds the route by longest path prefix and method; 404 if none
5. **Auth**: hashes `X-API-Key`, looks it up in the in-memory key map; 401 if missing, unknown or revoked; 403 if the consumer is blocked
6. **Rate limit**: runs the token-bucket script in Redis; 429 if empty
7. **Proxy**: forwards to the upstream with the route's timeout

**Config store**

A background goroutine polls `/internal/config` every 5 seconds. It builds a new immutable snapshot (route list sorted by prefix length, key map by hash) and swaps it in with `atomic.Pointer`. Request handlers read the pointer without locks. If a poll fails, the old snapshot stays in use.

**Token bucket**

Each key has a bucket with capacity equal to its per-minute limit, refilled continuously. One Lua script does the whole check so two gateway instances cannot both spend the last token:

```lua
-- KEYS[1] = rl:{key_id}   ARGV = capacity, refill_per_sec, now_ms
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1]) or tonumber(ARGV[1])
local ts = tonumber(b[2]) or tonumber(ARGV[3])
local elapsed = math.max(0, tonumber(ARGV[3]) - ts) / 1000
tokens = math.min(tonumber(ARGV[1]), tokens + elapsed * tonumber(ARGV[2]))
local allowed = tokens >= 1
if allowed then tokens = tokens - 1 end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', ARGV[3])
redis.call('EXPIRE', KEYS[1], 120)
return { allowed and 1 or 0, math.floor(tokens) }
```

The gateway sets `X-RateLimit-Limit` and `X-RateLimit-Remaining` on every response, and `Retry-After` on a 429. If Redis is unreachable, the gateway lets the request through and logs a warning (fail open).

**Error responses**

| Status | When | Body |
| --- | --- | --- |
| 401 | Key missing, unknown or revoked | `{"error":"invalid_api_key"}` |
| 403 | Consumer blocked | `{"error":"consumer_blocked"}` |
| 404 | No route matches | `{"error":"route_not_found"}` |
| 429 | Rate limit hit | `{"error":"rate_limit_exceeded"}` |
| 502 | Upstream refused or reset the connection | `{"error":"bad_gateway"}` |
| 504 | Upstream exceeded the route timeout | `{"error":"gateway_timeout"}` |

**Log line**

```json
{"ts":"2026-11-15T16:00:00Z","request_id":"a1b2","method":"GET","path":"/v1/orders/7","route_id":"r1","consumer_id":"c1","status":200,"latency_ms":12,"upstream_ms":9}
```

Logs use Go's `log/slog` with the JSON handler. The API key itself never appears in a log.

## 8. Frontend

The frontend is 14 pages in one React app: 9 admin pages under `/admin` and 5 developer portal pages under `/portal`. Claude builds it against the control plane's OpenAPI spec, starting once the MVP API is stable.

**Admin dashboard**

| # | Page | Path | Contains | Phase |
| --- | --- | --- | --- | --- |
| 1 | Login | `/admin/login` | Email and password form, error state | v2a |
| 2 | Overview | `/admin` | Stat cards (requests, error rate, p95, active consumers), 24-hour traffic chart, top routes and consumers, recent errors | v2b |
| 3 | Services | `/admin/services` | Table, add and edit modal, delete | v2a |
| 4 | Service detail | `/admin/services/:id` | Editable info, its routes, traffic chart | v2b |
| 5 | Routes | `/admin/routes` | Table, add modal with methods, service and auth toggle | v2a |
| 6 | Consumers | `/admin/consumers` | Table with status and plan, add, block and unblock | v2a |
| 7 | Consumer detail | `/admin/consumers/:id` | Info, API keys list, generate key (shown once), revoke, usage chart | v2a |
| 8 | Plans | `/admin/plans` | Plan cards, create and edit modal | v2b |
| 9 | Logs | `/admin/logs` | Filters, request table, detail drawer | v2b |

**Developer portal**

| # | Page | Path | Contains | Phase |
| --- | --- | --- | --- | --- |
| 10 | Sign up and login | `/portal/login` | Developer account forms | v3 |
| 11 | API catalog | `/portal/apis` | Cards for published APIs | v3 |
| 12 | API docs | `/portal/apis/:id` | Rendered OpenAPI docs, try-it console | v3 |
| 13 | My keys | `/portal/keys` | Create, list, revoke own keys | v3 |
| 14 | My usage | `/portal/usage` | Requests over time, quota bar, plan | v3 |

**Phases**

- **v2a** needs only the MVP API, so it can start as soon as the MVP is done. The usage chart on page 7 stays empty until analytics exist.
- **v2b** needs the plans and analytics endpoints.
- **v3** needs developer accounts in the control plane.

**Shared layout**

Sidebar (Overview, Services, Routes, Consumers, Plans, Logs), top bar with admin name and logout, light and dark theme.

**Folder structure**

```
frontend/src/
├── api/          # generated types and the fetch client
├── components/   # shared UI: tables, modals, charts, layout
├── features/     # one folder per page group: services, routes, consumers, ...
├── routes/       # route definitions and auth guard
└── lib/          # helpers: formatting, auth token storage
```

## 9. Repo structure and local setup

Everything lives in one repository and starts with `docker compose up`.

```
api-platform/
├── backend/
│   ├── gateway/                  # Go, one package per responsibility
│   │   ├── cmd/gateway/main.go
│   │   └── internal/
│   │       ├── config/           # poller and snapshot store
│   │       ├── middleware/       # recover, requestid, logger, auth, ratelimit
│   │       ├── proxy/            # route matching and reverse proxy
│   │       └── ratelimit/        # Redis client and Lua script
│   ├── control-plane/            # FastAPI, organized by feature
│   │   ├── app/
│   │   │   ├── core/             # settings, db session, security, shared deps
│   │   │   ├── auth/             # same four files (models = admins)
│   │   │   ├── services/         # router.py, models.py, schemas.py, crud.py
│   │   │   ├── routes/           # same four files
│   │   │   ├── consumers/        # same four files
│   │   │   ├── keys/             # same four files
│   │   │   ├── workspaces/       # models.py, crud.py (router comes with MT)
│   │   │   ├── gateway_config/   # the /internal/config endpoint
│   │   │   └── main.py           # creates the app, includes each router
│   │   ├── alembic/
│   │   └── tests/                # one test file per feature
│   └── mock-backend/             # small FastAPI echo service
├── frontend/                     # React dashboard (v2), organized by feature
├── loadtest/                     # k6 scripts
├── docs/
├── docker-compose.yml
└── README.md
```

Services are grouped into `backend/` and `frontend/` so it is easy to see which side each one is on. Each service folder is still its own program with its own container. (Changed on Nov 15, 2026 from a flat layout.)

The control plane is organized by feature: everything about one feature (endpoints, models, schemas, logic) sits in one folder, so a new feature such as `plans` in v2 is one new folder. Logic files are named `crud.py` rather than `service.py` so they don't clash with the `services` feature. The gateway keeps Go's package-per-responsibility layout.

**Docker Compose services**

| Service | Port | Purpose |
| --- | --- | --- |
| `postgres` | 5432 (host 5434) | Config database. Mapped to 5434 on the host because local Postgres installs already use 5432 and 5433 |
| `redis` | 6379 | Rate-limit state |
| `control-plane` | 8000 | Admin API and internal config endpoint |
| `gateway` | 8080 | Public entry point |
| `mock-backend` | 9000 | Echoes requests so there is something to proxy to |
| `dashboard` | 5173 | Admin UI (v2) |

**Environment variables**

| Service | Variable | Example |
| --- | --- | --- |
| Control plane | `DATABASE_URL` | `postgresql+asyncpg://app:app@postgres:5432/apim` |
| Control plane | `JWT_SECRET` | random 32 bytes |
| Control plane | `JWT_EXPIRE_MINUTES` | optional, default `60` |
| Control plane | `INTERNAL_TOKEN` | shared secret with the gateway |
| Control plane | `ADMIN_EMAIL`, `ADMIN_PASSWORD` | seeds the first admin |
| Gateway | `CONTROL_PLANE_URL` | `http://control-plane:8000` |
| Gateway | `INTERNAL_TOKEN` | same shared secret |
| Gateway | `REDIS_URL` | `redis://redis:6379/0` |
| Gateway | `CONFIG_POLL_INTERVAL` | `5s` |
| Gateway | `LISTEN_ADDR` | `:8080` |

Secrets go in a `.env` file that is listed in `.gitignore`. A committed `.env.example` shows the names without values.

## 10. Roadmap

The MVP takes about five weeks of part-time work: one week of Go basics, then four weeks of building. Week counts are estimates and assume a few sessions per week.

| Milestone | Weeks | Outcome | Who |
| --- | --- | --- | --- |
| Step 0: Go basics | 1 | Ali can read and write simple Go HTTP code | Ali |
| M1: Skeleton | 2 | Repo and Compose stack run; control plane has auth and services CRUD | Together |
| M2: Control plane done | 3 | All MVP endpoints, tests, config snapshot | Together |
| M3: Gateway proxies | 4 | Requests flow through the gateway with key auth | Together |
| M4: MVP done | 5 | Rate limiting, logging, load test, README | Together |
| MT: Multi-tenancy | after M4 | Several workspaces, roles, per-tenant gateway routing | Together |
| v2a: Admin UI | 6 to 7 | Pages 1, 3, 5, 6, 7 working against the API | Claude |
| v2b: Plans and analytics | 8 to 10 | Plans, usage events, metrics, remaining admin pages | Together, UI by Claude |
| v3: Portal and extras | 11 onward | Developer portal, JWT, LLM gateway mode | Together, UI by Claude |

**Step 0: Go basics**

- [ ] Install Go and finish [A Tour of Go](https://go.dev/tour/)
- [ ] Write a small HTTP server with `net/http` that returns JSON
- [ ] Write one middleware function that logs each request
- [ ] Try `httputil.NewSingleHostReverseProxy` against any local server

**M1: Skeleton**

- [x] Create the repo with the folder layout from section 9
- [x] Compose file with Postgres, Redis and the mock backend
- [x] FastAPI project with settings, async SQLAlchemy and Alembic
- [x] `workspaces` and `admins` tables, seed workspace and admin, login endpoint, JWT dependency
- [x] `services` model, migration and CRUD endpoints
- [ ] First pytest tests for login and services

**M2: Control plane done**

- [ ] `routes`, `consumers` and `api_keys` models and migrations
- [ ] CRUD endpoints for routes and consumers
- [ ] Key generation (shown once, hashed) and revocation
- [ ] `config_version` bump on every change
- [ ] `/internal/config` with the shared-secret check and ETag
- [ ] Tests for every endpoint

**M3: Gateway proxies**

- [ ] Go module, `main.go`, graceful shutdown, `/health`
- [ ] Config poller and atomic snapshot store
- [ ] Route matching by longest prefix and method
- [ ] Reverse proxy with per-route timeout, 502 and 504 handling
- [ ] Request ID, recover and logger middleware
- [ ] API key auth middleware with 401 and 403
- [ ] Unit tests for matching and auth

**M4: MVP done**

- [ ] Token-bucket Lua script and the Go wrapper around it
- [ ] Rate-limit middleware with headers and 429
- [ ] End-to-end test script: create service, route, consumer, key, then call through the gateway
- [ ] k6 load test and a note of the p95 latency the gateway adds
- [ ] README with setup steps and an architecture picture

**MT: Multi-tenancy (after the MVP)**

- [ ] Workspaces API: create and list workspaces
- [ ] Admin roles per workspace (owner, member) and inviting admins
- [ ] Make route `path_prefix` unique per workspace
- [ ] Gateway finds the tenant (e.g. by subdomain) and matches only that tenant's routes
- [ ] `/internal/config` snapshot grouped by workspace
- [ ] Isolation tests: workspace A can never read or change workspace B's data

**v2a: Admin UI (Claude)**

- [ ] Vite project, layout, auth guard, generated API types
- [ ] Login, Services, Routes, Consumers, Consumer detail
- [ ] CORS enabled on the control plane for the dashboard origin

## 11. Testing and observability

Each service has its own tests, and one end-to-end script proves the whole stack works together.

| Level | Tool | Covers |
| --- | --- | --- |
| Control plane unit and API tests | pytest, httpx, a test Postgres database | Every endpoint, key hashing, config snapshot |
| Gateway unit tests | `go test`, `httptest` | Route matching, auth, error mapping, middleware order |
| Rate limiter tests | `go test` against a real Redis in Compose | Bucket refill, burst, two clients sharing one key |
| End to end | Shell or pytest script against the Compose stack | 200, 401, 403, 404, 429, 502, 504 |
| Load | k6 | Throughput and p95 latency with and without the gateway |

**Observability**

- **MVP:** structured JSON logs from both services, with the request ID carried from the gateway to the backend.
- **v2:** Prometheus metrics from the gateway (`requests_total` by route and status, a latency histogram, `ratelimited_total`, config version and last sync time) and a Grafana dashboard.
- **v3:** OpenTelemetry traces across gateway and backend.

## 12. Risks and open questions

The main risk is scope: the full feature list is months of work, so nothing from v2 starts before the MVP runs end to end.

| Risk | What we do about it |
| --- | --- |
| Go learning curve slows the gateway | Step 0 first; standard library only; small pieces with tests |
| Rate limiter is wrong under concurrency | All logic in one Lua script; a test with parallel clients |
| Config changes take up to 5 seconds to apply | Accepted for the MVP; Redis pub/sub push later |
| Revoked keys keep working until the next poll | Same 5-second window; documented in the README |
| Request logs grow fast in Postgres (v2) | Monthly partitions and a retention job; consider ClickHouse only if needed |
| Frontend drifts from the API | Types generated from the OpenAPI spec on every change |

**Open questions**

- [ ] Should the rate limit live on the consumer (MVP) or move to plans from day one?
- [x] One admin account, or several admins with roles? *One seeded admin in the MVP. Several admins with roles per workspace come in MT.*
- [ ] Where will the demo run: local only, or a small cloud VM?
- [ ] Should usage events go from the gateway over HTTP batches or a Redis stream (v2)?
