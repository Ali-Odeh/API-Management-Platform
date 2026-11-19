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
- **Long polling for config.** The gateway asks for config and the control plane holds the request open until the config changes (or 30 seconds pass). Changes reach the gateway in milliseconds, idle traffic is one request per 30 seconds, and it is still plain HTTP with no new infrastructure. If anything goes wrong, the timeout makes it behave like ordinary polling. (Decided Nov 17, 2026; it replaces 5-second polling. Streaming over WebSocket or gRPC, as Kong and Envoy do, stays a possible v3 topic.)
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

Every admin change bumps `config_version`, done by Postgres triggers so no code path can forget it. The same trigger sends `NOTIFY config_changed`. The gateway calls `/internal/config?wait=30` with the version it already has:

- A newer version exists: the full snapshot comes back at once, and the gateway swaps it in atomically.
- Same version: the control plane holds the request. When a `NOTIFY` arrives it answers immediately with the new snapshot; after 30 seconds without a change it answers `304`.
- Either way the gateway asks again right away. On an error it waits a few seconds before retrying and keeps serving with its last snapshot.

## 4. Features by phase

The MVP is the smallest set that makes one request flow end to end. v2 turns it into a product, and v3 adds the stand-out features.

**Gateway (Go)**

| Feature | What it does | Phase |
| --- | --- | --- |
| Reverse proxy | Forwards `/v1/orders/*` to the `orders` backend | MVP |
| Route matching | Longest path prefix plus HTTP method | MVP |
| API key auth | Checks `X-API-Key`, returns 401 on a bad key | MVP |
| Rate limiting | Token bucket per consumer in Redis, 429 with `Retry-After` | MVP |
| Request logging | One JSON line per request | MVP |
| Config sync | Long-polls the control plane; changes arrive in milliseconds | MVP |
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

**Tenant-ready:** every config table has a `workspace_id` (FK to `workspaces`, not null, indexed). In the MVP only the `default` workspace exists. All queries live in each feature's `crud.py` and filter by the current admin's workspace, so turning on real multi-tenancy (milestone MT) needs no data migration. Child rows point to their parent with a composite foreign key on (`parent_id`, `workspace_id`), so the database itself makes a cross-workspace link (a route to another tenant's service, a key to another tenant's consumer) impossible.

**Delete rules:** a service that still has routes can't be deleted (409). Deleting a consumer deletes its API keys. `consumers.status` is text with a CHECK (`active` or `blocked`) rather than a Postgres ENUM, so new values are easy to add.

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
| `config_version` | `id` (always 1), `version` (bigint, starts at 1), `updated_at` | Single row (a CHECK forbids a second one). No uuid and no `workspace_id`: it is system state, not tenant config. Bumped by triggers, see below |

**How `config_version` is bumped**

A Postgres trigger function `bump_config_version()` runs after any `INSERT`, `UPDATE` or `DELETE` statement on `services`, `routes`, `consumers` and `api_keys`. It adds 1 to `version`, sets `updated_at`, and sends `NOTIFY config_changed` with the new version. Because it runs inside the same transaction, a failed change (for example a 409) bumps nothing and notifies nobody, and database cascades (deleting a consumer deletes its keys) are covered too. `admins` and `workspaces` don't bump: the gateway doesn't use them.

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
| `rl:{consumer_id}` | Hash (`tokens`, `ts`) | Token bucket state per consumer, shared by all of its keys | 2 minutes idle |
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

- Lists take `?page=` and `?page_size=` (max 100) and return `{ items, total }`, newest first. Some lists also filter: routes by `?service_id=`, consumers by `?status=`
- Errors return `{ "detail": "..." }` with the right status code: 404 unknown id (or another workspace's), 409 duplicate or still in use, 422 invalid input or a reference to something not in this workspace
- `PATCH` changes only the fields sent; sending `null` is rejected
- A route `path_prefix` of `/health` or below it (`/health/db`) is reserved by the gateway, which answers it itself, and is rejected with 422. `/healthy` and `/api/health` are fine. v2 adds `/metrics`
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

The `key` field appears in this response only, which is sent with `Cache-Control: no-store`. Later reads return the prefix, plus `is_active` and `revoked_at`.

**Key rules:** keys come from Python's `secrets` module (128 random bits). A consumer can have at most 10 active keys (409 after that). `DELETE /api/v1/keys/{id}` revokes: it sets `revoked_at` and keeps the row, and revoking again is a no-op. A revoked key can't be restored; generate a new one instead. Keys can be generated for a blocked consumer, since blocking is checked per request.

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

**Details (step 2.5):**

- `?wait=` (0 to 60 seconds, default 0) turns on long polling: with an unchanged version the request is held until a `NOTIFY config_changed` arrives or the time runs out. The gateway sends `wait=30`.
- `If-None-Match` accepts `"46"`, `W/"46"` or `46`. A missing or unreadable value gets the full snapshot.
- Missing or wrong `X-Internal-Token` is `401`. An admin JWT does not work here.
- The snapshot holds active routes on active services and non-revoked keys. Keys of blocked consumers stay in with `consumer_status: "blocked"`, so the gateway can answer `403`. Routes are sorted by `path_prefix` and keys by `id`.
- The version and the data are read in one `REPEATABLE READ` transaction, so they always match. Each version's snapshot is built once and shared by every gateway that asks for it.
- Each control-plane process keeps one dedicated Postgres connection for `LISTEN`, reconnecting on its own if it drops. After waking or timing out, the version is re-read from the database, so a lost notification can delay an update but never hide it.

## 7. Gateway design

The gateway is a chain of middleware around one reverse proxy. Each middleware either rejects the request or passes it on.

**Middleware order**

1. **Request ID**: keeps a safe client `X-Request-ID` (1–128 chars, `A-Z a-z 0-9 . _ -`) or creates one; passes it upstream and back to the client
2. **Logger**: records start time, writes the log line when the response is done (also for answers the proxy aborts)
3. **Recover**: catches panics and returns 500 instead of crashing. It sits inside the logger (changed in step 3.5; the plan first had it outermost) so the panic's 500 appears in the request log; the proxy's deliberate `ErrAbortHandler` passes through
4. **Route match**: finds the route by longest path prefix and method; 404 if no path fits, 405 if a path fits but no route allows the method
5. **Auth**: hashes `X-API-Key`, looks it up in the in-memory key map; 401 if missing, unknown or revoked; 403 if the consumer is blocked
6. **Rate limit**: runs the token-bucket script in Redis; 429 if empty
7. **Proxy**: forwards to the upstream with the route's timeout

**Route matching (step 3.3)**

- Routes are checked longest `path_prefix` first; the first whose prefix fits **on whole path segments** and that allows the method wins. So `/v1/orders` covers `/v1/orders/7` but not `/v1/orders-archive`, and a request falls back to a shorter route (`/v1`) when the longer one lacks its method.
- Methods match exactly (HEAD is not implied by GET). Paths are case-sensitive.
- Each request reads the snapshot once; matching and the key check use that one version. The match is passed on in the request context.
- Unclean paths: Go's server redirects `/v1/../x` and `/v1//x` (307) to the clean form; encoded ones (`%2e%2e`) are rejected with 400, so no request can reach a path outside its route's prefix.
- `GET /health` is the gateway's own; every other request is API traffic.
- A plain scan of the sorted list: fast for hundreds of routes. MT adds the workspace as a first filter.

**API key auth (step 3.6)**

- Runs after route matching (404 before 401, as in Kong) and only on routes with `auth_required`. It uses the same snapshot the router used.
- The key is read from the `X-API-Key` header only (a key in the query string doesn't count), hashed with SHA-256 (lowercase hex, exactly like the control plane) and looked up in the snapshot's key map.
- No key: `401 missing_api_key`. Unknown, revoked or doubled key: `401 invalid_api_key` (unknown and revoked look the same on purpose). Blocked consumer: `403 consumer_blocked`. Every 401 carries `WWW-Authenticate: ApiKey header="X-API-Key"`.
- On every route, `X-API-Key` and any client-sent `X-Consumer-ID` are removed before forwarding (Kong's `hide_credentials`, turned on). A valid key adds `X-Consumer-ID` for the backend.
- The log line gets `consumer_id` and `key_id` (also for a blocked consumer); the key itself never appears.
- Later: limiting failed attempts per IP (after the rate limiter), `last_used_at` (v2 usage events), JWT (v3).

**Proxy (step 3.4)**

- One `httputil.ReverseProxy` and one connection pool serve every route; the target comes from the matched route per request, so config changes apply to the next request.
- Path: `strip_prefix` removes the prefix (the bare prefix becomes `/`), the upstream's base path is joined in front (`http://svc/api` + `/7` → `/api/7`), the query is kept, and encoded characters (`%2F`, `%20`) reach the backend exactly as sent.
- Headers: hop-by-hop headers and any client-sent `Forwarded` / `X-Forwarded-*` are removed (a client could fake them); fresh `X-Forwarded-For`, `-Host` and `-Proto` are set. The backend sees its own `Host`.
- The route's `timeout_ms` covers the whole answer (as in Envoy). Over it: `504`. Refused, reset or unknown host: `502`. A connect or TLS timeout (5 s each) also counts as `504`. A client that hangs up gets nothing; the status is recorded as `499` for the log (nginx's convention).
- Connection pool: up to 100 idle connections per backend (Go's default is 2) and 1000 in total, closed after 90 s unused. No `HTTP_PROXY` from the environment.
- Not yet: retries (useful with several backend instances, v3 circuit breaker), a request body limit, WebSockets (the whole-answer timeout would cut them).

**Config store**

A background goroutine long-polls `/internal/config?wait=30` in a loop (section 3). It builds a new immutable snapshot (route list sorted by prefix length, key map by hash) and swaps it in with `atomic.Pointer`. Request handlers read the pointer without locks. If a request fails, the old snapshot stays in use and the goroutine retries after a short backoff.

Details (step 3.2):

- The server starts without waiting for config. Until the first snapshot arrives the store is empty; step 3.3 answers such requests with `503 {"error":"config_not_loaded"}`.
- Backoff after a failure: 1 s, 2 s, 4 s ... up to 30 s, each with random jitter (half to all of the value), reset after a success. A `304` is not a failure: the next request goes out at once, but never more than one per 250 ms.
- The HTTP client timeout is `CONFIG_WAIT` + 10 s, so a held request is never cut short. Shutdown cancels a held request at once.
- A body that isn't valid JSON or has no `version` is rejected whole. A single bad route or key is skipped with a warning and the rest applied, so one bad row can't block other changes such as key revocations. Unknown JSON fields are ignored.
- A lower version than the current one is accepted (for example after a database reset): the control plane is the source of truth.

**Token bucket**

Each consumer has one bucket with capacity equal to its `rate_limit_per_min`, refilled continuously. All of a consumer's keys share that bucket, because the limit is set on the consumer: a bucket per key would let a consumer multiply its limit just by creating more keys (changed Nov 16, 2026). One Lua script does the whole check so two gateway instances cannot both spend the last token:

```lua
-- KEYS[1] = rl:{consumer_id}   ARGV[1] = capacity (requests per minute)
local capacity = tonumber(ARGV[1])
local refill_per_ms = capacity / 60000
local t = redis.call('TIME')   -- Redis's clock: one clock for every gateway
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1]) or capacity
local ts = tonumber(b[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * refill_per_ms)
local allowed, retry_after_ms = 0, 0
if tokens >= 1 then tokens = tokens - 1; allowed = 1
else retry_after_ms = math.ceil((1 - tokens) / refill_per_ms) end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], 120000)
return { allowed, math.floor(tokens), retry_after_ms }
```

The full, commented script is `backend/gateway/internal/ratelimit/tokenbucket.lua` (step 4.1). Two changes from the first draft: the script reads **Redis's clock** (`TIME`) instead of a time passed in by the gateway, so gateways whose clocks differ can't confuse a bucket; and it returns **`retry_after_ms`**, the exact time until the next token, for `Retry-After`. The Go side (`ratelimit.Limiter.Allow`) uses go-redis (`EVALSHA`, falling back to `EVAL` when Redis has forgotten the script) with a 50 ms timeout: a slower Redis counts as down.

**Rate-limit middleware (step 4.2)**

- Runs after auth: it needs the consumer and its `rate_limit_per_min` (from the snapshot), and requests refused with 401/403 cost no budget. Public routes (`auth_required = false`) have no consumer and are not limited in the MVP; a per-IP limit for them is a v2 idea.
- Allowed: `X-RateLimit-Limit` and `X-RateLimit-Remaining` on the response. Empty bucket: `429 {"error":"rate_limit_exceeded"}` with the same headers and `Retry-After` in whole seconds (rounded up, at least 1).
- Fail open: if Redis doesn't answer within 50 ms, the request goes through without rate-limit headers. The warning is throttled to one line per 10 s, with a count of the held-back ones, so an outage can't flood the log. After Redis comes back, limiting resumes within about 3 s (go-redis retries dialing in the background instead of on every request).
- The request log's `ratelimit` field is `""` (not limited), `"ok"`, `"limited"` (429) or `"unavailable"` (Redis down, let through).
- The Redis client connects lazily (Redis down at startup is fine), has 50 ms read/write/pool timeouts and no retries.

**Error responses**

| Status | When | Body |
| --- | --- | --- |
| 401 | No `X-API-Key` header (with `WWW-Authenticate`) | `{"error":"missing_api_key"}` |
| 401 | Key unknown or revoked (same answer for both), or sent twice | `{"error":"invalid_api_key"}` |
| 403 | Consumer blocked | `{"error":"consumer_blocked"}` |
| 400 | Path with an encoded `.` or `..` segment (`%2e%2e`) | `{"error":"invalid_path"}` |
| 404 | No route matches | `{"error":"route_not_found"}` |
| 405 | A route's path fits but none allows the method; `Allow` lists the methods | `{"error":"method_not_allowed"}` |
| 503 | No config loaded yet (gateway just started); `Retry-After: 1` | `{"error":"config_not_loaded"}` |
| 429 | Rate limit hit (with `Retry-After` and `X-RateLimit-*`) | `{"error":"rate_limit_exceeded"}` |
| 502 | Upstream refused or reset the connection | `{"error":"bad_gateway"}` |
| 504 | Upstream exceeded the route timeout | `{"error":"gateway_timeout"}` |

**Log line**

```json
{"ts":"2026-11-15T16:00:00Z","level":"INFO","msg":"request","request_id":"a1b2","method":"GET","path":"/v1/orders/7","route_id":"r1","consumer_id":"c1","key_id":"k1","ratelimit":"ok","status":200,"latency_ms":12.4,"upstream_ms":9.1,"gateway_ms":3.3,"client_ip":"172.18.0.1","bytes":245}
```

Logs use Go's `log/slog` with the JSON handler. The API key itself never appears in a log, and neither does the query string (it can hold tokens or personal data). Times are milliseconds with microsecond precision. `upstream_ms` is the wait until the backend's response headers; `gateway_ms` = `latency_ms` − `upstream_ms` is the time the gateway added. `status` 499 means the client hung up; `aborted: true` marks an answer cut off mid-stream. `GET /health` is not logged. A panic adds an `ERROR` line with the stack trace and the request ID.

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
│   │       ├── ratelimit/        # Redis client and Lua script
│   │       ├── reqctx/           # per-request facts the log needs (request ID, route, times)
│   │       ├── server/           # http.Server, timeouts, /health, graceful shutdown
│   │       ├── respond/          # the gateway's own JSON answers, {"error": "..."}
│   │       └── settings/         # env vars, validated at startup
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
├── e2e/                          # end-to-end tests (pytest) against the whole Compose stack
├── loadtest/                     # k6 load test (gateway.js) and a log helper; results in loadtest/README.md
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
| `gateway` | 8080 (host 8081) | Public entry point. Mapped to 8081 on the host because another local app already uses 127.0.0.1:8080 |
| `mock-backend` | 9000 | Echoes requests so there is something to proxy to; `?delay_ms=N` answers late, to see a `504` |
| `dashboard` | 5173 | Admin UI (v2) |

**Environment variables**

| Service | Variable | Example |
| --- | --- | --- |
| Control plane | `DATABASE_URL` | `postgresql+asyncpg://app:app@postgres:5432/apim` |
| Control plane | `JWT_SECRET` | random 32 bytes |
| Control plane | `JWT_EXPIRE_MINUTES` | optional, default `60` |
| Control plane | `INTERNAL_TOKEN` | shared secret with the gateway |
| Control plane | `ADMIN_EMAIL`, `ADMIN_PASSWORD` | seeds the first admin |
| Gateway | `CONTROL_PLANE_URL` | `http://control-plane:8000` (required) |
| Gateway | `INTERNAL_TOKEN` | same shared secret (required; never logged) |
| Gateway | `REDIS_URL` | `redis://redis:6379/0` (required; `rediss://` for TLS; treated as a secret, since it may hold a password) |
| Gateway | `CONFIG_WAIT` | `30s` (how long the control plane may hold a config request; whole seconds, 1 to 60) |
| Gateway | `LISTEN_ADDR` | `:8080` (set in `docker-compose.yml`, next to the port mapping that depends on it) |
| Gateway | `SHUTDOWN_TIMEOUT` | optional, default `5s` (time given to in-flight requests on stop) |

The gateway container gets only the variables it uses, not the whole `.env`: it never needs the database password or the JWT secret.

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
- [x] First pytest tests for login and services (plus ruff, mypy strict and coverage, run by `scripts/check.ps1`)

**M2: Control plane done**

- [x] `routes`, `consumers` and `api_keys` models and migrations
- [x] CRUD endpoints for routes and consumers
- [x] Key generation (shown once, hashed) and revocation
- [x] `config_version` bump on every change (Postgres triggers + NOTIFY)
- [x] `/internal/config` with the shared-secret check, ETag and long polling
- [x] Tests for every endpoint (plus guard tests that discover every endpoint, an end-to-end admin flow, the committed OpenAPI contract and CI on GitHub Actions)

**M3: Gateway proxies**

- [x] Go module, `main.go`, graceful shutdown, `/health`
- [x] Config poller and atomic snapshot store
- [x] Route matching by longest prefix and method
- [x] Reverse proxy with per-route timeout, 502 and 504 handling
- [x] Request ID, recover and logger middleware
- [x] API key auth middleware with 401 and 403
- [x] Unit tests for matching and auth (plus a whole-gateway test, a load test during config changes, fuzz tests, benchmarks and a 90% coverage gate)

**M4: MVP done**

- [x] Token-bucket Lua script and the Go wrapper around it
- [x] Rate-limit middleware with headers and 429
- [x] End-to-end test script: create service, route, consumer, key, then call through the gateway
- [x] k6 load test and a note of the p95 latency the gateway adds (about 1 ms at p95; see loadtest/README.md)
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
| Gateway unit tests | `go test`, `httptest` | Route matching, auth, error mapping, middleware order; the whole program start to stop against a fake control plane; requests under load while the config changes (`-race`); coverage at least 90% |
| Gateway fuzz tests | `go test -fuzz` | Path checks, matching, prefix stripping and request IDs against independent reference rules; 10 s each in CI |
| Gateway benchmarks | `go test -bench` (by hand) | Matching vs. number of routes, key lookup vs. number of keys, the whole chain without network |
| Rate limiter tests | `go test` against a real Redis (Docker locally, database 15; a Redis service in CI); skipped without `REDIS_TEST_URL` | Burst, refill, `retry_after`, separate buckets, lowered limit, expiry, script reload, 100 concurrent requests for 50 tokens |
| End to end | pytest + httpx in `e2e/`, run in its own container (`docker compose run --rm e2e`) and in CI on a fresh stack | The MVP story: admin API creates service, routes, consumers and keys, then calls through the gateway: 200 (backend sees `X-Consumer-ID`, not the key), 401, 403 after a block, 401 after a revoke, a new route live within 1 s, 404, 405, 429, 502, 504, `strip_prefix`, public routes. Own objects, always cleaned up |
| Load | k6 in Docker (`docker compose run --rm loadtest`); a 5 s smoke run in CI | Three runs at the same fixed rate: direct to the backend, through the gateway on a public route, and on a protected route (key + Redis). Measured at 300 requests/s: the gateway adds 0.77 ms median and 0.95 ms p95 in total; its own code about 0.08 ms, the Redis rate limit about 0.3 ms, the rest is the extra network hop. 0 errors |

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
| Config changes must reach the gateway fast | Long polling woken by Postgres `NOTIFY`: milliseconds in normal operation |
| Revoked keys keep working while the gateway can't reach the control plane | The gateway keeps its last snapshot by design; documented in the README. Changes apply as soon as it reconnects |
| Request logs grow fast in Postgres (v2) | Monthly partitions and a retention job; consider ClickHouse only if needed |
| Frontend drifts from the API | Types generated from the OpenAPI spec on every change |

**Open questions**

- [ ] Should the rate limit live on the consumer (MVP) or move to plans from day one?
- [x] One admin account, or several admins with roles? *One seeded admin in the MVP. Several admins with roles per workspace come in MT.*
- [ ] Where will the demo run: local only, or a small cloud VM?
- [ ] Should usage events go from the gateway over HTTP batches or a Redis stream (v2)?

**Patterns to apply later (reminder)**

Config sync doesn't need these: the change and the version bump share one transaction, and the gateway receives full snapshots, which are naturally idempotent. Later milestones do need them:

| When | Pattern | What it solves |
| --- | --- | --- |
| v2 usage events (gateway → control plane batches) | **Inbox** | Each batch carries an id; the control plane records processed ids and skips repeats, so a retried batch is never counted twice |
| v3 webhooks (e.g. tell a customer "key revoked") | **Outbox** | The change and an outbox row are written in one transaction; a worker delivers with retries, so a webhook is never lost or sent for a change that rolled back |
| v3 audit log | **Outbox** (same idea) | "Who changed what" is written in the same transaction as the change |
