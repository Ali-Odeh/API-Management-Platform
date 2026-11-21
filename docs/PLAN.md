# API Management Platform: plan and architecture

Nov 15, 2026 · @Ali · updated Nov 20, 2026 for multi-tenancy (MT)

This file says **what** is built and **how**. The reasons behind the MT design (decisions 0–17, with the options that were not chosen) are in [MT_ROLES_DECISIONS.md](MT_ROLES_DECISIONS.md); progress is in [ROADMAP.md](ROADMAP.md).

## 1. Overview and goals

The platform is a self-hosted API management system: a Go gateway that every API request passes through, a FastAPI control plane that configures it, and a React dashboard to manage it. It is a learning project modeled on Kong, Tyk and Apigee.

**Learning goals**

- Learn Go by building a real reverse proxy with auth and rate limiting
- Practice distributed-systems basics: Redis counters, config propagation, caching
- Practice system design: data plane vs. control plane, middleware chains
- End with a portfolio project that runs with one `docker compose up`

**MVP definition of done**

An admin registers a service and a route through the admin API, creates a consumer with an API key, and that consumer calls the backend through the gateway. The request is authenticated, rate-limited and logged. Bad keys get 401, and clients over the limit get 429.

**MT definition of done**

A Platform Admin (with MFA) creates two workspaces, each with its own Owner. Each Owner adds team members with roles (Owner, API Admin, Viewer). Both workspaces publish a route on the same path (`/v1/orders`), each with several backend targets; their consumers call `acme.<gateway>` and `globex.<gateway>` and reach only their own backends. No member, key or request of one workspace can read, change or reach the other's data or backends. Every admin change appears in the workspace's audit log. A suspended workspace's traffic is refused; a deleted one can be restored for 30 days.

**Out of scope for now**

- Billing and payments
- Kubernetes deployment and multi-region setups
- OAuth2 authorization server, mTLS
- Postgres Row-Level Security, custom roles, service accounts, SSO, custom domains (all deferred; the design leaves room for them, see MT_ROLES_DECISIONS.md)

**How we work**

| Part | Who builds it | Where |
| --- | --- | --- |
| Gateway (Go) | Ali with an AI coding agent | Claude Code or Codex |
| Control plane (FastAPI) | Ali with an AI coding agent | Claude Code or Codex |
| Authentication | GoTrue (Supabase Auth), self-hosted | Docker Compose |
| Frontend (React) | Claude | Built **last, all at once**, after every backend milestone, against the final control-plane API |

The agent works through one roadmap step at a time and finishes the whole step on its own (code, tests, docs, logical commits) before handing back. Rules are in `AGENTS.md`.

## 2. Tech stack and decisions

| Layer | Choice | Why |
| --- | --- | --- |
| Gateway | Go 1.23+, standard library (`net/http`, `httputil.ReverseProxy`) | Built for network services; no framework hides how the proxy works |
| Control plane | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2 | Fastest way to build CRUD; opens the door to AI features later |
| Database | PostgreSQL 16 | Source of truth for all config |
| Cache and counters | Redis 7 | Atomic rate-limit counters through Lua scripts |
| Authentication (MT) | GoTrue (Supabase Auth), self-hosted, own `auth` database | Proven login, refresh rotation, MFA, invitations and recovery; we build authorization |
| Email (MT) | Mailpit locally and in CI; any SMTP in production | GoTrue's invitation and recovery emails |
| Frontend | React, TypeScript, Vite, TanStack Query, React Router, shadcn/ui, Tailwind, Recharts | Standard modern stack with typed API calls |
| Local environment | Docker Compose | One command starts everything |
| Load testing | k6 | Scriptable; reports p95 and p99 latency |
| Metrics (v2) | Prometheus and Grafana | Industry standard |

**Key decisions**

- **Two services, two languages.** The gateway is on the hot path and must be fast. The control plane is CRUD and should be quick to change.
- **The gateway never touches Postgres.** It pulls config from the control plane over HTTP and keeps it in memory. If the control plane goes down, the gateway keeps serving with its last config.
- **Long polling for config.** The gateway asks for config and the control plane holds the request open until the config changes (or 30 seconds pass). Changes reach the gateway in milliseconds, idle traffic is one request per 30 seconds, and it is still plain HTTP with no new infrastructure. If anything goes wrong, the timeout makes it behave like ordinary polling. (Decided Nov 17, 2026; it replaces 5-second polling. Streaming over WebSocket or gRPC, as Kong and Envoy do, stays a possible v3 topic.)
- **No Go web framework.** The standard library is enough, and it is what teaches the most.
- **Ready-made authentication, our own authorization (MT).** GoTrue says who a person is; the control plane decides what they may do, from its own tables, on every request. No token exchange.
- **One shared gateway for all workspaces (MT).** Every gateway instance serves every workspace (pooled model), protected by per-workspace limits. A dedicated gateway per large workspace stays possible later by filtering the snapshot.

## 3. System architecture

The system splits into a data plane that handles live traffic and a control plane that holds configuration.

```mermaid
flowchart LR
  subgraph clients[Clients]
    C1[Consumer apps<br/>X-API-Key]
    U[Dashboard users]
  end
  subgraph data[Data plane]
    GW[Gateway, Go<br/>several instances]
    R[(Redis<br/>rate-limit buckets)]
  end
  subgraph control[Control plane]
    CP[Control plane, FastAPI]
    PG[(Postgres<br/>apim database)]
    AUTH[GoTrue<br/>auth database]
    MAIL[Mailpit / SMTP]
  end
  B1[Workspace A backends<br/>targets]
  B2[Workspace B backends<br/>targets]

  C1 -- "acme.gw.example.com" --> GW
  GW -- token buckets --> R
  GW -- "weighted round robin,<br/>health checks" --> B1
  GW --> B2
  GW -- "long poll<br/>/internal/config?since=seq" --> CP
  U -- login, MFA --> AUTH
  U -- "admin API + GoTrue JWT" --> CP
  CP --> PG
  CP -- "admin API: invite, ban" --> AUTH
  AUTH --> MAIL
```

Live traffic flows left to right through the gateway. Configuration is written through the control plane and pulled by the gateway, which never talks to Postgres directly. People log in with GoTrue; the control plane trusts GoTrue's token for **who** someone is and its own tables for **what** they may do.

**Data plane (gateway)**

- Handles every API request of every workspace, so it must be fast and stay up
- Keeps a snapshot of all workspaces in memory (hosts, routes, targets, key hashes, limits); reads Redis only for rate-limit state
- Stateless, so several instances run behind a load balancer; each one health-checks the backends on its own

**Control plane**

- Owns all configuration in Postgres and exposes the admin API, scoped by workspace in the URL
- Verifies GoTrue JWTs and checks the member's role on every request
- Serves the gateway only the workspaces that changed since the gateway's last sequence number
- Can be down for a while without affecting live traffic

**Authentication (GoTrue)**

- Login, refresh-token rotation, logout, MFA (TOTP), password recovery, invitation emails
- No public sign-up: people join only by being added to a workspace
- Never involved in gateway traffic

**Request flow (gateway)**

1. A client sends `GET https://acme.gw.example.com/v1/orders/7` with `X-API-Key`.
2. The gateway finds the workspace from the `Host` (no subdomain → `default`). Unknown host → 404; suspended workspace → 403.
3. It matches the path against **that workspace's** routes only (no match → 404 or 405), answers a CORS preflight, and checks the body size against the route's limit (→ 413).
4. On a protected route it hashes the key and looks it up; the key's workspace must equal the Host's workspace. Bad key → 401; blocked consumer → 403.
5. One Redis script checks the consumer's bucket and the workspace's bucket. Empty → 429. The workspace's in-flight limit is checked in memory → 503 when full.
6. It picks a healthy target by weighted round robin, checks the target's IP against the SSRF policy at connect time, and forwards with the route's timeout.
7. It returns the response and writes one log line, including `workspace_id`.

**Config propagation**

Every admin change runs Postgres triggers in the same transaction. They bump the global sequence `config_seq`, bump the changed workspace's `version`, record that sequence on the workspace, and send `NOTIFY config_changed`. The gateway calls `/internal/config?since=<seq>&wait=30` with the last sequence it saw:

- Workspaces changed after that sequence: they come back at once, each complete, and the gateway merges them into a new snapshot that reuses every unchanged workspace, then swaps it in atomically.
- Nothing changed: the control plane holds the request until a `NOTIFY` arrives (answers at once) or 30 seconds pass (`304`).
- `since=0` (gateway start), a sequence newer than the database's (e.g. after a reset), or anything unexpected: a **full** answer with every workspace.
- Either way the gateway asks again right away. On an error it backs off and keeps serving with its last snapshot.

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
| Tenant from Host | `{slug}.<gateway>` → workspace; that workspace's routes only | MT |
| Workspace checks | Key must belong to the Host's workspace; suspended workspace → 403; trusted `X-Workspace-ID` to the backend | MT |
| Workspace limits | Requests per minute per workspace (Redis, same script as consumers); in-flight requests per workspace (memory) | MT |
| SSRF protection | Internal IPs blocked at connect time; allow list set by the Platform Admin | MT |
| Load balancing | Several targets per service, weighted round robin | MT |
| Health checks | Active (every 10 s) and passive (from real traffic) per target | MT |
| CORS | Per route; preflight answered by the gateway | MT |
| Body size limit | Platform default, per-route override; 413 | MT |
| Incremental config | Receives only the workspaces that changed | MT |
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
| Admin auth | Email and password login, JWT access token (replaced by GoTrue in MT) | MVP |
| Services CRUD | Name, upstream URL, timeout | MVP |
| Routes CRUD | Path prefix, methods, service, auth flag | MVP |
| Consumers CRUD | Name, email, status, rate limit | MVP |
| API keys | Generate, list, revoke; shown once, stored hashed | MVP |
| Gateway config endpoint | One snapshot of everything the gateway needs | MVP |
| Plans | Rate limit and monthly quota tiers | v2 |
| Usage analytics | Requests, errors and latency by consumer and route | v2 |
| GoTrue authentication | Verify GoTrue JWTs; users created just in time; MFA required for the Platform Admin | MT |
| Workspaces and roles | Several workspaces; Owner, API Admin, Viewer per membership; permission checks on every endpoint | MT |
| Members | Add by email (GoTrue invite for new people), change role, remove, leave; Owner rules | MT |
| Workspace lifecycle | Create (Platform Admin), suspend, delete with 30-day restore, purge | MT |
| Workspace quotas | Max services, routes, consumers; adjustable by the Platform Admin | MT |
| Audit log | Who changed what, and when; Platform Admin actions included | MT (moved from v3) |
| Service targets | Several backend URLs per service with weights and health-check settings | MT |
| Route CORS and body limit | Per-route settings | MT |
| API lifecycle | Draft, published, deprecated | v3 |
| Developer accounts | Sign-up and self-service keys for the portal | v3 |

## 5. Database design

Two databases live on the same Postgres server: **`apim`** (ours, migrated by Alembic) and **`auth`** (GoTrue's, migrated by GoTrue). They never share tables; our `users` rows point to GoTrue users by id. Every table of ours has a `uuid` primary key plus `created_at` and `updated_at` timestamps, except where noted.

**Tenant isolation:** every config table has a `workspace_id` (FK to `workspaces`, not null, indexed). Child rows point to their parent with a composite foreign key on (`parent_id`, `workspace_id`), so the database itself makes a cross-workspace link (a route to another workspace's service, a key to another workspace's consumer) impossible. All queries live in each feature's `crud.py`; every function takes `workspace_id` as a required first parameter, and automatic isolation tests prove that no endpoint crosses workspaces. Postgres Row-Level Security is deferred; the schema already allows it.

**Delete rules:** a service that still has routes can't be deleted (409). Deleting a service deletes its targets. Deleting a consumer deletes its API keys. `status` and `role` columns are text with a CHECK rather than Postgres ENUMs, so new values are easy to add. Workspaces are never hard-deleted (see `workspaces.status`).

Every table also has `created_at` and `updated_at` (not repeated below), except `audit_events`: events never change, so it has `created_at` only. GoTrue's `auth` database is not shown: our only link to it is `users.auth_user_id`.

```mermaid
erDiagram
  USERS ||--o{ WORKSPACE_MEMBERS : holds
  WORKSPACES ||--o{ WORKSPACE_MEMBERS : has
  WORKSPACES ||--|| WORKSPACE_CONFIG : versioned_by
  WORKSPACES ||--o{ SERVICES : owns
  WORKSPACES ||--o{ CONSUMERS : owns
  SERVICES ||--|{ SERVICE_TARGETS : has
  SERVICES ||--o{ ROUTES : serves
  CONSUMERS ||--o{ API_KEYS : owns
  WORKSPACES ||--o{ AUDIT_EVENTS : records
  WORKSPACES {
    uuid id PK
    string name
    string slug UK
    string status
    string status_reason
    timestamptz deleted_at
    timestamptz purge_after
    int rate_limit_per_min
    int max_concurrent
    int max_services
    int max_routes
    int max_consumers
  }
  USERS {
    uuid id PK
    uuid auth_user_id UK
    string email
    bool is_platform_admin
    bool is_active
  }
  WORKSPACE_MEMBERS {
    uuid id PK
    uuid workspace_id FK
    uuid user_id FK
    string role
    uuid added_by FK
  }
  WORKSPACE_CONFIG {
    uuid workspace_id PK
    bigint version
    bigint seq
  }
  SERVICES {
    uuid id PK
    uuid workspace_id FK
    string name
    int timeout_ms
    bool is_active
    string lb_algorithm
    string hc_path
    int hc_interval_s
    int hc_unhealthy_threshold
    int hc_healthy_threshold
    int passive_failures
    int passive_eject_s
  }
  SERVICE_TARGETS {
    uuid id PK
    uuid workspace_id FK
    uuid service_id FK
    string url
    int weight
    bool is_active
  }
  ROUTES {
    uuid id PK
    uuid workspace_id FK
    uuid service_id FK
    string path_prefix
    text_array methods
    bool strip_prefix
    bool auth_required
    bool is_active
    bigint max_body_bytes
    text_array cors_origins
    text_array cors_methods
    text_array cors_headers
    bool cors_credentials
    int cors_max_age
  }
  CONSUMERS {
    uuid id PK
    uuid workspace_id FK
    string name
    string email
    string status
    int rate_limit_per_min
  }
  API_KEYS {
    uuid id PK
    uuid workspace_id FK
    uuid consumer_id FK
    string key_prefix
    string key_hash UK
    string name
    timestamptz last_used_at
    timestamptz revoked_at
  }
  AUDIT_EVENTS {
    uuid id PK
    uuid workspace_id
    uuid actor_user_id
    string actor_email
    string actor_type
    string action
    string target_type
    uuid target_id
    string target_name
    jsonb changes
    string request_id
    inet ip
    string user_agent
  }
  CONFIG_SEQ {
    int id PK
    bigint seq
  }
```

`text_array` is Postgres `text[]` (Mermaid can't write brackets). v2 adds `plans` and `request_logs` (below).

**Constraints and indexes (part of the design, not left to the implementation)**

| Table | Rule |
| --- | --- |
| `workspaces` | `slug` unique (`varchar(50)`, as today), CHECK `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$` and 3–50 chars (a valid DNS label), immutable; reserved slugs refused with 422: `www`, `api`, `app`, `admin`, `platform`, `portal`, `auth`, `login`, `mail`, `status`, `docs`, `static`, `internal`, `gateway`, `health`; `status` CHECK in (`active`, `suspended`, `deleted`, `purged`); limit columns NULL or > 0 |
| `users` | `auth_user_id` unique; NULL only for a row made before its GoTrue account was resolved (the migrated MVP admin, until startup links it); `email` unique and always stored lowercase, CHECK `email = lower(email)` |
| `workspace_members` | unique (`workspace_id`, `user_id`); `role` CHECK in (`owner`, `api_admin`, `viewer`); index on `user_id` (for "my workspaces") |
| `services` | unique (`workspace_id`, `name`); unique (`id`, `workspace_id`) as the target of composite FKs; `lb_algorithm` CHECK in (`round_robin`); positive health-check numbers |
| `service_targets` | FK (`service_id`, `workspace_id`) → `services` (`id`, `workspace_id`), ON DELETE CASCADE; unique (`service_id`, `url`); `weight` 1–1000 |
| `routes` | FK (`service_id`, `workspace_id`) → `services`, ON DELETE RESTRICT (409); unique (`workspace_id`, `path_prefix`) **replaces** today's global `uq_routes_path_prefix`; the existing CHECKs stay (methods known and not empty, `path_prefix` starts with `/` and has no trailing `/`); `max_body_bytes` NULL or > 0 |
| `consumers` | unique (`workspace_id`, `name`) and unique (`id`, `workspace_id`) (both exist today); `status` CHECK in (`active`, `blocked`); `rate_limit_per_min` CHECK > 0 (exists), at most 100,000 (schema); index on (`workspace_id`, `status`) |
| `api_keys` | FK (`consumer_id`, `workspace_id`) → `consumers`, ON DELETE CASCADE; `key_hash` unique; CHECKs `char_length(key_hash) = 64` and `char_length(key_prefix) = 12` (exist today); index on `consumer_id` (exists; a partial index WHERE `revoked_at IS NULL` is optional) |
| `workspace_config` | PK and FK `workspace_id`; index on `seq` (the `since` query) |
| `config_seq` | single row: CHECK `id = 1` |
| `audit_events` | no FK on `workspace_id` (platform rows survive purges); `actor_type` CHECK in (`user`, `platform_admin`, `system`); indexes on (`workspace_id`, `created_at` DESC) and (`created_at`); the app's database user has INSERT and SELECT only |
| every tenant table | index on `workspace_id`; every query filters by it; the FK to `workspaces` stays ON DELETE RESTRICT (as today), so the purge job deletes children first: `api_keys`, `routes`, `service_targets`, `services`, `consumers`, `workspace_members`, the workspace's `audit_events`, `workspace_config` |
| every table | `updated_at` is set by a `BEFORE UPDATE` trigger (`set_updated_at`), not by the ORM, so updates from triggers, bulk statements and SQL keep it correct; it moves only when the row really changed (MT.1) |

The agent writes the Alembic migrations to match this exactly; column lengths and index names are its choice.

**Moving from the MVP schema** (decision 13; the existing data stays in `default`)

| MVP today | MT | Step |
| --- | --- | --- |
| `admins` (`workspace_id`, `email`, `password_hash`) | `users` + `workspace_members` (Owner of `default`, `is_platform_admin = true`); `password_hash` dropped | MT.1, MT.2 |
| `updated_at` set only by the ORM | `set_updated_at` trigger on every table | MT.1 |
| `uq_routes_path_prefix` (global) | unique (`workspace_id`, `path_prefix`) | MT.2 |
| `workspaces` (`name`, `slug`) | slug format CHECK and reserved list (`default` passes) | MT.2 (moved from MT.5: workspaces can be created from MT.2 on) |
| `workspaces` | + `status`, lifecycle and limit columns | MT.5 |
| `config_version` + one statement trigger per table | `config_seq` + `workspace_config` + three transition-table triggers per table (above) | MT.6 |
| `services.upstream_url` | one row per service in `service_targets` (weight 100); column dropped | MT.10 |
| — | `audit_events` | MT.3 |
| — | route CORS and `max_body_bytes` columns | MT.11 |

**Tables after MT**

| Table | Columns | Notes |
| --- | --- | --- |
| `workspaces` | `name`, `slug` (unique), `status` (`active`, `suspended`, `deleted`, `purged`), `status_reason`, `deleted_at`, `purge_after`, `rate_limit_per_min`, `max_concurrent`, `max_services`, `max_routes`, `max_consumers` | `slug` is immutable and never reused (it is the gateway subdomain). Limit columns are nullable: NULL = the platform default. `default` can't be suspended or deleted |
| `users` | `auth_user_id` (unique, GoTrue's `sub`), `email`, `is_platform_admin`, `is_active` | Replaces `admins`. Created just in time on the first request with a new GoTrue JWT and bound to that account's id for good. A row is **never** linked by a token's email: a token from another account whose email a row already holds gets `403 account_conflict`. Rows made before their first login (no GoTrue id yet) are linked only through GoTrue's admin API (exact email): the Platform Admin at startup, and anyone being disabled or enabled. Once bound, a row is never rebound automatically; rebinding is an explicit operator action (`python -m scripts.relink_user <email>`). For a disabled user, GoTrue must confirm the replacement account's ban before the binding is saved. Not tenant data: no `workspace_id` |
| `workspace_members` | `workspace_id` (FK), `user_id` (FK), `role` (`owner`, `api_admin`, `viewer`), `added_by` | Unique on (`workspace_id`, `user_id`). One person can be a member of several workspaces with different roles |
| `services` | `workspace_id` (FK), `name`, `timeout_ms` (default 5000), `is_active`, `lb_algorithm` (`round_robin`), `hc_path` (default `/health`), `hc_interval_s` (10), `hc_unhealthy_threshold` (3), `hc_healthy_threshold` (2), `passive_failures` (5), `passive_eject_s` (30) | Unique on (`workspace_id`, `name`). `upstream_url` moved to `service_targets` |
| `service_targets` | `workspace_id` (FK), `service_id` (FK), `url`, `weight` (1–1000, default 100), `is_active` | Unique on (`service_id`, `url`). At least one active target for a service to be routable |
| `routes` | `workspace_id` (FK), `service_id` (FK), `path_prefix`, `methods` (text array), `strip_prefix`, `auth_required`, `is_active`, `max_body_bytes` (NULL = default), `cors_origins` (text array, empty = CORS off), `cors_methods`, `cors_headers`, `cors_credentials`, `cors_max_age` | `path_prefix` unique **per workspace** (was global in the MVP); the gateway separates workspaces by Host |
| `consumers` | `workspace_id` (FK), `name`, `email`, `status` (`active` or `blocked`), `rate_limit_per_min` (default 60) | Unchanged |
| `api_keys` | `workspace_id` (FK), `consumer_id` (FK), `key_prefix`, `key_hash` (unique), `name`, `last_used_at`, `revoked_at` | Unchanged; the raw key is never stored |
| `config_seq` | `id` (always 1), `seq` (bigint), `updated_at` | Single row (a CHECK forbids a second one). Replaces `config_version`. System state: no uuid, no `workspace_id` |
| `workspace_config` | `workspace_id` (PK, FK), `version` (bigint), `seq` (bigint, indexed) | One row per workspace. `seq` = the global sequence at the workspace's last change |
| `audit_events` | `workspace_id` (nullable), `actor_user_id`, `actor_email`, `actor_type` (`user`, `platform_admin`, `system`), `action`, `target_type`, `target_id`, `target_name`, `changes` (jsonb), `request_id`, `ip`, `user_agent` | Append-only (see below). `workspace_id` NULL = platform-level event. No FK on `workspace_id`, so platform-level rows survive a purge |

**How the config sequence is bumped**

Statement-level triggers run after every `INSERT`, `UPDATE` and `DELETE` statement on `services`, `service_targets`, `routes`, `consumers`, `api_keys` and `workspaces`. They use the statement's **transition tables** (`NEW TABLE`, `OLD TABLE`) to collect the distinct `workspace_id`s that changed. Then, once per statement, `bump_config()` adds 1 to `config_seq`, and for each of those workspaces adds 1 to `workspace_config.version` and sets `workspace_config.seq` to the new sequence, and sends `NOTIFY config_changed` with the new sequence. Because it runs inside the same transaction, a failed change bumps nothing and notifies nobody, and database cascades are covered too. `users`, `workspace_members` and `audit_events` don't bump: the gateway doesn't use them.

Postgres rules this design must follow (checked against the Postgres 16 docs):

- A trigger with transition tables can't list several events with `OR`, so each table gets **three triggers** (insert, update, delete), all calling the same function.
- A trigger with transition tables can't use `UPDATE OF <columns>`. So the update trigger **filters inside the function**: it joins `OLD TABLE` and `NEW TABLE` on `id` and only counts rows whose **config columns** changed (`IS DISTINCT FROM`). Writes that touch only `updated_at`, `last_used_at` (v2 usage events) or, on `workspaces`, only `name`, bump nothing.
- An update that moves no row (e.g. a PATCH with the same values) bumps nothing.
- `INSERT` into `workspaces` also creates its `workspace_config` row (version 1).

**Audit rows are append-only**

The application's database user has only `INSERT` and `SELECT` on `audit_events`. A retention job (default 1 year), running as a separate database user, is the only thing that deletes rows. Each change and its audit row are written in the same transaction. Audit events must exclude secrets. The log records keys by prefix and strips userinfo from upstream URLs before recording old and new values.

**Database logins (MT.3).** The API can't connect as the owner, a superuser that ignores every grant. Three logins:

| Login | Setting | May |
| --- | --- | --- |
| the owner (`POSTGRES_USER`) | `MIGRATION_DATABASE_URL` | everything: migrations and setup only |
| `apim_app` | `DATABASE_URL` | read and write every table, but only `INSERT` and `SELECT` on `audit_events`; never `alembic_version` |
| `apim_retention` | `AUDIT_RETENTION_DATABASE_URL` | `SELECT` and `DELETE` on `audit_events`, nothing else |

The one-shot `control-plane-db-init` service creates or updates the two logins with `scripts/prepare_database.py` and runs migrations before the API starts. Migrations grant table privileges, including defaults for later tables. The API container receives only the `apim_app` login for Postgres.

**v2 tables**

| Table | Columns | Notes |
| --- | --- | --- |
| `plans` | `workspace_id` (FK), `name`, `rate_limit_per_min`, `monthly_quota` | Replaces the per-consumer limit; `consumers` gets `plan_id` |
| `request_logs` | `ts`, `workspace_id`, `consumer_id`, `route_id`, `method`, `path`, `status`, `latency_ms` | Partition by month; feeds the analytics pages |

**API key format**

A key looks like `ak_live_` followed by 32 random characters. The control plane stores the first 12 characters as `key_prefix` for display and the SHA-256 hash of the full key as `key_hash`. SHA-256 is right here because keys are long and random, so slow password hashes add nothing.

**Redis keys**

| Key | Type | Purpose | Expiry |
| --- | --- | --- | --- |
| `rl:{consumer_id}` | Hash (`tokens`, `ts`) | Token bucket per consumer, shared by all of its keys | 2 minutes idle |
| `rlws:{workspace_id}` | Hash (`tokens`, `ts`) | Token bucket per workspace, checked in the same script | 2 minutes idle |
| `quota:{consumer_id}:{yyyymm}` | Counter | Monthly quota (v2) | 35 days |

If Redis Cluster is ever used, the two keys of one script must share a hash slot (e.g. a `{workspace_id}` hash tag in both).

## 6. Control plane API

The admin API lives under `/api/v1`. Every call carries a **GoTrue access token** (`Authorization: Bearer <jwt>`). Workspace resources live under `/api/v1/workspaces/{slug}/...`. The gateway uses one separate internal endpoint protected by a shared secret.

**How a request is authorized**

1. **Who:** verify the GoTrue JWT (signature, expiry, audience `authenticated`, a UUID `sub` and an email). Missing or invalid → `401`. The `users` row is found by `sub`, or created just in time from the token's claims (`403 account_conflict` if another account's row holds that email). `users.is_active = false` → `403 user_disabled`.
2. **Which workspace and which role:** one query resolves the slug and the caller's membership. Unknown workspace, a deleted one, or not a member → `404` (it doesn't reveal that the workspace exists). The Platform Admin passes this step for every workspace.
3. **May they:** the endpoint declares a permission, e.g. `Annotated[WorkspaceAccess, require_permission(Permission.SERVICES_WRITE)]`. Not allowed → `403 permission_denied`. A Platform Admin may act in every workspace; where their own role there (if any) doesn't hold the permission, that platform power needs MFA (`403 mfa_required` without `aal2`), as on the platform endpoints. A suspended workspace refuses every write with `403 workspace_suspended`.

The role is read from the database on every request, so a removed member or a lowered role takes effect on the very next request.

**Roles and permissions**

Roles are ordered `owner > api_admin > viewer`. Each endpoint checks a permission, never a role; the role → permissions map is a constant in code (it can move to tables later for custom roles without touching endpoints).

| Permission | viewer | api_admin | owner |
| --- | --- | --- | --- |
| `services:read`, `routes:read`, `consumers:read`, `keys:read`, `members:read`, `workspace:read` | ✅ | ✅ | ✅ |
| `services:write`, `routes:write`, `consumers:write`, `keys:write` | | ✅ | ✅ |
| `audit:read` | | ✅ | ✅ |
| `members:write`, `workspace:write`, `workspace:delete` | | | ✅ |

The **Platform Admin** (`users.is_platform_admin`) may do everything in every workspace, plus the platform endpoints. Platform endpoints require MFA: a token without `aal: "aal2"` → `403 mfa_required`; anyone else → `403 platform_admin_required`. A 403 carries a short code as `detail` (`user_disabled`, `account_conflict`, `mfa_required`, `platform_admin_required`, `permission_denied`) so the dashboard can act on it.

**Endpoints**

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v1/me` | signed in | Current user |
| GET | `/api/v1/me/workspaces` | signed in | My workspaces and my role in each |
| DELETE | `/api/v1/me/workspaces/{slug}` | member | Leave a workspace (not the last owner) |
| GET, POST | `/api/v1/workspaces` | Platform Admin | List all, create `{name, slug, owner_email}` |
| GET | `/api/v1/workspaces/{slug}` | `workspace:read` | Workspace details, limits, status |
| PATCH | `/api/v1/workspaces/{slug}` | `workspace:write` | Rename (`name` only; the slug never changes) |
| PATCH | `/api/v1/workspaces/{slug}/limits` | Platform Admin | Rate limit, concurrency and quotas |
| POST | `/api/v1/workspaces/{slug}/suspend`, `/unsuspend` | Platform Admin | Suspend `{reason}`, unsuspend |
| DELETE | `/api/v1/workspaces/{slug}` | `workspace:delete` | Delete `{confirm: "<slug>"}`; restorable for 30 days |
| POST | `/api/v1/workspaces/{slug}/restore` | Platform Admin | Restore a deleted workspace |
| GET, POST | `/api/v1/workspaces/{slug}/members` | `members:read`, `members:write` | List; add `{email, role}` |
| PATCH, DELETE | `/api/v1/workspaces/{slug}/members/{user_id}` | `members:write` | Change role, remove |
| GET, POST | `/api/v1/workspaces/{slug}/services` | `services:read`, `services:write` | List, create (with targets) |
| GET, PATCH, DELETE | `/api/v1/workspaces/{slug}/services/{id}` | same | Read, update, delete |
| GET, POST | `/api/v1/workspaces/{slug}/services/{id}/targets` | same | List, add a target |
| PATCH, DELETE | `/api/v1/workspaces/{slug}/services/{id}/targets/{target_id}` | `services:write` | Update weight or state, remove |
| GET, POST | `/api/v1/workspaces/{slug}/routes` | `routes:*` | List, create |
| GET, PATCH, DELETE | `/api/v1/workspaces/{slug}/routes/{id}` | `routes:*` | Read, update, delete |
| GET, POST | `/api/v1/workspaces/{slug}/consumers` | `consumers:*` | List, create |
| GET, PATCH, DELETE | `/api/v1/workspaces/{slug}/consumers/{id}` | `consumers:*` | Read, update (including block), delete |
| GET, POST | `/api/v1/workspaces/{slug}/consumers/{id}/keys` | `keys:*` | List keys, generate a key |
| DELETE | `/api/v1/workspaces/{slug}/keys/{id}` | `keys:write` | Revoke a key |
| GET | `/api/v1/workspaces/{slug}/audit-events` | `audit:read` | The workspace's audit log |
| GET | `/api/v1/audit-events` | Platform Admin | All audit events |
| PATCH | `/api/v1/users/{id}` | Platform Admin | Disable or enable a user `{is_active}`; also bans or unbans them in GoTrue (an account not linked yet is found by exact email). The row is locked across the GoTrue calls. Disabling yourself → `409`. GoTrue failing or timing out (the lookup or the ban) → `502` with the safe side kept: a disable stays (repeat to finish the ban), an enable isn't made |
| GET | `/internal/config` | internal token | Config for the gateway |
| GET | `/health` | none | Liveness |

There is no login endpoint: the dashboard logs in with GoTrue directly. The MVP's unscoped URLs (`/api/v1/services` and the rest) and `/api/v1/auth/*` are removed.

**Conventions**

- Lists take `?page=` and `?page_size=` (max 100) and return `{ items, total }`, newest first. Some lists also filter: routes by `?service_id=`, consumers by `?status=`, audit events by `?actor=`, `?action=`, `?target_type=`, `?from=`, `?to=`. A filter naming an object that isn't in this workspace (another workspace's, or none) → `422`, as in a body
- Errors return `{ "detail": "..." }` with the right status code: 401 not signed in, 403 not allowed, 404 unknown id or workspace (or another workspace's), 409 duplicate, still in use, `last_owner` or `quota_exceeded`, 422 invalid input or a reference to something not in this workspace
- `PATCH` changes only the fields sent; sending `null` is rejected
- A route `path_prefix` of `/health` or below it (`/health/db`) is reserved by the gateway and rejected with 422. `/healthy` and `/api/health` are fine. v2 adds `/metrics`
- Upstream and target URLs: only `http` and `https`, without a query string, a fragment, a username or a password (each → 422: a password would be stored, returned by the API and sent to the gateway, which ignores it); an internal IP literal or a known internal name (`localhost`, `*.internal`) not covered by `UPSTREAM_ALLOW` → 422. The gateway enforces the real check at connect time (section 7)
- Every write records an audit event in the same transaction (section 5): exactly one per change; a request that changes nothing (a PATCH with the same values, revoking a revoked key) records none. Actions are `<target>.<verb>` (`service.create`, `route.update`, `consumer.delete`, `key.create`, `key.revoke`, `user.create`, `user.disable`, `user.enable`, `user.relink`, `member.add`), lifecycle events as listed below (`workspace.created`). `actor_type` is `platform_admin` when only platform power allowed the change (and on platform endpoints), `user` within the person's own role, `system` for startup and scripts
- Every response carries `X-Request-ID`: the client's when it is 1–128 characters of `A-Z a-z 0-9 . _ -`, else a new one; audit events store it with the IP and user agent
- Audit lists filter by `?actor=` (an email, any case), `?action=`, `?target_type=`, `?from=` (at or after) and `?to=` (before); the platform list also by `?workspace_id=`
- FastAPI generates the OpenAPI spec, committed as `openapi.json`; the frontend generates its TypeScript types from it

**Members and owners**

- `POST /members {email, role}` (Owner): an existing user is added at once; a new person gets a GoTrue invitation email, sets a password, and is a member from their first login. Their `users` row and membership are made right away, bound to the GoTrue account (found, or invited, through the admin API), so no invitations table is needed; an account GoTrue already has gets no second email. The answer says `invited: true|false`. Adding an existing member → 409; a disabled person → 422.
- `POST /api/v1/workspaces` invites a new `owner_email` the same way.
- Several owners are allowed. An owner can change anyone's role except their own (`409`).
- A workspace always keeps at least one **active, human** owner: demoting, removing or the leaving of the last one → `409 last_owner`. Owner rows are locked (`SELECT ... FOR UPDATE`, in id order) before any role change, removal or leave. Disabling a user (`PATCH /users`) is not blocked by this rule: a compromised account must be stoppable at once; the Platform Admin then assigns a new owner.
- The Platform Admin can assign an owner in any workspace, by adding or promoting someone (audited as `member.owner_assigned_by_platform`, visible to the workspace's owners), and follows the same rule.

**Workspace lifecycle**

| Status | Gateway | Admin API |
| --- | --- | --- |
| `active` | Serves traffic | Normal |
| `suspended` | `403 workspace_suspended` | Read-only for members |
| `deleted` | Removed from the config (`404` on its host) | Hidden from members (`404`); the Platform Admin can restore within 30 days |
| `purged` | — | A daily job deleted all of its data; the row stays so the slug is never reused |

Lifecycle events (`workspace.created`, `suspended`, `unsuspended`, `deleted`, `restored`, `purged`) are platform-level audit events (`workspace_id` NULL, `target_id` = the workspace).

**Quotas** (defaults, adjustable per workspace by the Platform Admin): 100 services, 500 routes, 5,000 consumers, 10 active keys per consumer. Over a quota → `409 quota_exceeded`.

**Generate a key**

```json
POST /api/v1/workspaces/acme/consumers/{id}/keys
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

**Key rules:** keys come from Python's `secrets` module (128 random bits). A consumer can have at most 10 active keys (409 after that). Revoking sets `revoked_at` and keeps the row; revoking again is a no-op. A revoked key can't be restored; generate a new one instead. Keys can be generated for a blocked consumer, since blocking is checked per request.

**Gateway config**

```json
GET /internal/config?since=1045&wait=30
X-Internal-Token: <shared secret>

200 OK
ETag: "1046"
{
  "seq": 1046,
  "full": false,
  "workspaces": [
    {
      "id": "ws_acme", "slug": "acme", "status": "active", "version": 18,
      "hosts": ["acme"],
      "limits": { "rate_limit_per_min": 6000, "max_concurrent": 200 },
      "services": [
        {
          "id": "s1", "timeout_ms": 5000, "lb_algorithm": "round_robin",
          "health": { "path": "/health", "interval_s": 10, "unhealthy": 3, "healthy": 2,
                      "passive_failures": 5, "passive_eject_s": 30 },
          "targets": [ { "id": "t1", "url": "http://orders-1:9000", "weight": 100 } ]
        }
      ],
      "routes": [
        {
          "id": "r1", "service_id": "s1", "path_prefix": "/v1/orders", "methods": ["GET", "POST"],
          "strip_prefix": false, "auth_required": true, "max_body_bytes": 10485760,
          "cors": { "origins": [], "methods": [], "headers": [], "credentials": false, "max_age": 600 }
        }
      ],
      "keys": [
        { "id": "k1", "key_hash": "9f86d0...", "consumer_id": "c1",
          "consumer_status": "active", "rate_limit_per_min": 60 }
      ]
    }
  ],
  "deleted": []
}
```

**Details:**

- `since` = the last `seq` the gateway applied (0 at start). Workspaces with `workspace_config.seq > since` come back, each **complete**. `full: true` (every workspace) when `since` is 0, newer than the database's sequence, or otherwise unusable.
- Nothing changed and `wait` > 0: the request is held until `NOTIFY config_changed` or the time runs out (`304`). `wait` is 0 to 60 seconds; the gateway sends 30.
- `deleted` lists workspaces that left the config (deleted or purged). Suspended workspaces stay in with `status: "suspended"`, so the gateway can answer 403.
- `hosts` holds the workspace's host labels (its slug today; custom domains later through `workspace_hosts`).
- `limits` and `max_body_bytes` are **effective** values: the workspace's or route's own setting, or the platform default. The gateway never needs the defaults itself.
- Missing or wrong `X-Internal-Token` → `401`. A user's JWT does not work here.
- Until the gateway tells workspaces apart by Host (MT.6, MT.7), the answer carries only the `default` workspace: path prefixes are unique only per workspace since MT.2, and another workspace's routes and keys would otherwise work on the gateway's bare host.
- Only active routes on active services with at least one active target, and non-revoked keys. Keys of blocked consumers stay in with `consumer_status: "blocked"`, so the gateway can answer `403`. Lists are sorted (routes by `path_prefix`, keys by `id`).
- The sequence and the data are read in one `REPEATABLE READ` transaction, so they always match. Each `(workspace_id, version)` is built once and cached for every gateway that asks.
- Each control-plane process keeps one dedicated Postgres connection for `LISTEN`, reconnecting on its own. After waking or timing out, the sequence is re-read from the database, so a lost notification can delay an update but never hide it.
- `?workspaces=acme,globex` (optional) limits the answer to those workspaces, for a dedicated gateway later.

## 7. Gateway design

The gateway is a chain of middleware around one reverse proxy. Each middleware either rejects the request or passes it on.

**Middleware order**

1. **Request ID**: keeps a safe client `X-Request-ID` (1–128 chars, `A-Z a-z 0-9 . _ -`) or creates one; passes it upstream and back to the client
2. **Logger**: records start time, writes the log line when the response is done (also for answers the proxy aborts)
3. **Recover**: catches panics and returns 500 instead of crashing. It sits inside the logger (changed in step 3.5; the plan first had it outermost) so the panic's 500 appears in the request log; the proxy's deliberate `ErrAbortHandler` passes through
4. **Tenant** (MT): finds the workspace from the `Host`; 404 if unknown, 403 if suspended. From here on everything uses that workspace only
5. **Route match**: finds the route among **the workspace's** routes by longest path prefix and method; 404 if no path fits, 405 if a path fits but no route allows the method (a CORS preflight is matched by path only)
6. **CORS** (MT): answers a preflight for a route with CORS on; adds CORS headers to normal answers
7. **Body limit** (MT): 413 if `Content-Length` is over the route's limit; wraps the body so an unannounced body is cut at the limit. Matching never reads the body, so checking after it is still before any upload is accepted
8. **Auth**: hashes `X-API-Key`, looks it up in the workspace's key map; 401 if missing, unknown, revoked or from another workspace; 403 if the consumer is blocked
9. **Rate limit**: one Redis script checks the consumer bucket and the workspace bucket; 429 if either is empty
10. **Concurrency** (MT): takes a slot of the workspace's in-flight limit; 503 when full
11. **Proxy**: picks a healthy target by weighted round robin and forwards with the route's timeout; the dialer refuses internal IPs (SSRF)

**Defaults live in the control plane.** Platform defaults (workspace rate limit, in-flight limit, body size) are control-plane settings; the snapshot carries each workspace's and each route's **effective** values, so the gateway needs no extra settings and a change of default reaches every gateway through the normal config flow.

**Route matching (step 3.3)**

- Routes are checked longest `path_prefix` first; the first whose prefix fits **on whole path segments** and that allows the method wins. So `/v1/orders` covers `/v1/orders/7` but not `/v1/orders-archive`, and a request falls back to a shorter route (`/v1`) when the longer one lacks its method.
- Methods match exactly (HEAD is not implied by GET). Paths are case-sensitive.
- Each request reads the snapshot once; matching and the key check use that one version. The match is passed on in the request context.
- Unclean paths: Go's server redirects `/v1/../x` and `/v1//x` (307) to the clean form; encoded ones (`%2e%2e`) are rejected with 400, so no request can reach a path outside its route's prefix.
- `GET /health` is the gateway's own; every other request is API traffic.
- A plain scan of the workspace's sorted list: fast for hundreds of routes (benchmarks: 4–5 µs for 1000). Two workspaces can both have `/v1/orders`; each only sees its own.

**Tenant resolution (MT)**

- The snapshot holds `host → workspace` and `workspace → (routes, keys, services, limits)`. The Host header's port is dropped; the first label is the workspace slug when the rest equals `GATEWAY_BASE_DOMAIN` (e.g. `acme.gw.example.com`, or `acme.localhost` locally).
- No subdomain (the bare base domain or `localhost`): the `default` workspace, so MVP clients and tests keep working.
- Unknown host or a workspace not in the snapshot: `404 {"error":"workspace_not_found"}`. Suspended: `403 {"error":"workspace_suspended"}`, before any route matching.
- The workspace is put on the per-request notes card (`reqctx`) together with the snapshot pointer, so every later step uses the same workspace and config version.
- `X-Workspace-ID` from the client is removed; the gateway sets a trusted one for the backend.
- Custom domains later: more host labels per workspace (`workspace_hosts`), same lookup.

**API key auth (step 3.6)**

- Runs after route matching (404 before 401, as in Kong) and only on routes with `auth_required`. It uses the same snapshot the router used.
- The key is read from the `X-API-Key` header only (a key in the query string doesn't count), hashed with SHA-256 (lowercase hex, exactly like the control plane) and looked up in **the Host's workspace's** key map. A valid key of another workspace is therefore unknown here: `401 invalid_api_key` (MT).
- No key: `401 missing_api_key`. Unknown, revoked or doubled key: `401 invalid_api_key` (unknown and revoked look the same on purpose). Blocked consumer: `403 consumer_blocked`. Every 401 carries `WWW-Authenticate: ApiKey header="X-API-Key"`.
- On every route, `X-API-Key` and any client-sent `X-Consumer-ID` and `X-Workspace-ID` are removed before forwarding (Kong's `hide_credentials`, turned on). A valid key adds `X-Consumer-ID` for the backend; every request gets the trusted `X-Workspace-ID`.
- The log line gets `consumer_id` and `key_id` (also for a blocked consumer); the key itself never appears.
- Later: limiting failed attempts per IP (after the rate limiter), `last_used_at` (v2 usage events), JWT (v3).

**Proxy (step 3.4)**

- One `httputil.ReverseProxy` and one connection pool serve every route; the target is picked per request by the route's service (load balancing, below), so config changes apply to the next request.
- Path: `strip_prefix` removes the prefix (the bare prefix becomes `/`), the upstream's base path is joined in front (`http://svc/api` + `/7` → `/api/7`), the query is kept, and encoded characters (`%2F`, `%20`) reach the backend exactly as sent.
- Headers: hop-by-hop headers and any client-sent `Forwarded` / `X-Forwarded-*` are removed (a client could fake them); fresh `X-Forwarded-For`, `-Host` and `-Proto` are set. The backend sees its own `Host`.
- The route's `timeout_ms` covers the whole answer (as in Envoy). Over it: `504`. Refused, reset or unknown host: `502`. A connect or TLS timeout (5 s each) also counts as `504`. A client that hangs up gets nothing; the status is recorded as `499` for the log (nginx's convention).
- Connection pool: up to 100 idle connections per backend (Go's default is 2) and 1000 in total, closed after 90 s unused. No `HTTP_PROXY` from the environment.
- Not yet: retries on another target, WebSockets (the whole-answer timeout would cut them).

**Load balancing and health checks (MT)**

- A service has one or more **targets** (`url`, `weight`). The gateway picks among the **healthy** targets with **smooth weighted round robin** (as nginx does): over time each target gets its share of requests in proportion to its weight, without bursts to one target.
- Balancer state (the round-robin counters, health) lives per service inside the gateway and survives config updates when the service's targets haven't changed; a changed target list starts fresh.
- **Active checks:** a goroutine per target calls `GET <target><hc_path>` every `hc_interval_s` (default 10 s) with a short timeout. `hc_unhealthy_threshold` (3) failures in a row → unhealthy; `hc_healthy_threshold` (2) successes in a row → healthy again. Any 2xx or 3xx counts as healthy.
- **Passive checks:** from real traffic, `passive_failures` (5) connection errors or 5xx answers in a row → the target is skipped for `passive_eject_s` (30 s); then it is tried again (and the active checks confirm it).
- New targets start healthy. Each gateway instance checks on its own; there is no shared health state.
- No healthy target: `503 {"error":"no_healthy_upstream"}`.
- Health checks go through the same SSRF dialer as traffic. Checkers stop when a target or its workspace leaves the config.
- The log line records `target_id`; v2 metrics expose target health.

**SSRF protection (MT)**

- The transport's `net.Dialer` has a `Control` hook that runs after DNS resolution and before connecting. It checks the real IP (IPv4-mapped IPv6 is unmapped first) against the egress policy, so DNS rebinding can't get around it.
- Blocked by default: `127.0.0.0/8`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`, `169.254.0.0/16`, `fe80::/10`, `0.0.0.0/8`, `100.64.0.0/10`, multicast.
- `UPSTREAM_ALLOW` (hostnames and CIDRs, set by the Platform Admin) makes exceptions; local Compose and CI allow `mock-backend`. A hostname in the list is allowed together with the addresses it resolves to.
- A refused connection: `502 {"error":"upstream_not_allowed"}` and a warning log line with the workspace and the target. Passive health checks don't count it as a target failure.
- The check runs once per new connection (the pool reuses connections), so it costs nothing per request. The policy function has a fuzz test.

**Workspace limits (MT)**

- **Requests per minute:** the rate-limit script checks the workspace bucket (`rlws:{workspace_id}`, capacity = the workspace's effective `rate_limit_per_min` from the snapshot) on every request, also on public routes, which had no limit before. On protected routes the same call also checks the consumer bucket; a token is spent only if both allow.
- **In flight:** a counting semaphore per workspace per gateway instance (the effective `max_concurrent` from the snapshot, default 200). Full → `503 {"error":"workspace_overloaded"}` with `Retry-After: 1`. Released when the response is done or the client hangs up. Per instance on purpose: it protects each instance from a slow backend.

**CORS (MT)**

- Off for a route unless `cors.origins` is set. An origin matches exactly; `*` is allowed only without credentials.
- **Preflight** (`OPTIONS` with `Origin` and `Access-Control-Request-Method`): matched by path regardless of the route's methods, answered by the gateway with `204` and the `Access-Control-Allow-*` and `Max-Age` headers, before auth (a browser never sends an API key on a preflight). Not forwarded and not rate-limited. A preflight for a route without CORS or from an unknown origin gets `204` without allow headers, so the browser blocks the call.
- **Normal requests** with an allowed `Origin`: the answer gets `Access-Control-Allow-Origin` (the origin itself), `Vary: Origin`, and `Access-Control-Allow-Credentials` when set.

**Body size limit (MT)**

- Platform default `MAX_REQUEST_BODY` (10 MB, a control-plane setting); a route may set `max_body_bytes` up to `MAX_REQUEST_BODY_CEILING` (100 MB, checked by the control plane with 422). The snapshot carries each route's effective limit.
- A `Content-Length` over the limit → `413 {"error":"request_too_large"}` at once, without reading the body. Bodies without a length are wrapped in `http.MaxBytesReader`; going over cuts the request with `413` if nothing was sent upstream yet, otherwise the upstream request is aborted and logged.

**Config store**

A background goroutine long-polls `/internal/config?since=<seq>&wait=30` in a loop (section 3). The snapshot is immutable: `Seq`, `ByHost` (host label → workspace) and `Workspaces` (id → an immutable per-workspace config: routes sorted by prefix length, key map by hash, services with targets, limits, status). For each answer it builds a **new** snapshot that reuses the pointers of every unchanged workspace, replaces the changed ones and drops the deleted ones, then swaps it in with `atomic.Pointer`. Request handlers read the pointer without locks. A `full` answer replaces everything. If a request fails, the old snapshot stays in use and the goroutine retries after a short backoff.

Details (step 3.2):

- The server starts without waiting for config. Until the first snapshot arrives the store is empty; step 3.3 answers such requests with `503 {"error":"config_not_loaded"}`.
- Backoff after a failure: 1 s, 2 s, 4 s ... up to 30 s, each with random jitter (half to all of the value), reset after a success. A `304` is not a failure: the next request goes out at once, but never more than one per 250 ms.
- The HTTP client timeout is `CONFIG_WAIT` + 10 s, so a held request is never cut short. Shutdown cancels a held request at once.
- A body that isn't valid JSON or has no `seq` is rejected whole. A single bad route, target or key is skipped with a warning and the rest applied, so one bad row can't block other changes such as key revocations. Unknown JSON fields are ignored.
- A lower sequence than the current one only comes as a `full` answer (for example after a database reset) and is accepted: the control plane is the source of truth. A non-full answer with a lower sequence is a protocol error: the gateway asks again with `since=0`.

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

**Two buckets in one script (MT).** The script takes one or two keys: `KEYS[1] = rlws:{workspace_id}` always, `KEYS[2] = rl:{consumer_id}` on protected routes, with each capacity in `ARGV`. It refills both, spends one token from each **only if both have one**, and returns `{ allowed, remaining_of_consumer_or_workspace, retry_after_ms, limited_by }` where `limited_by` is `"workspace"` or `"consumer"`. One round trip, as before. Public routes run it with the workspace key only.

**Rate-limit middleware (step 4.2)**

- Runs after auth: it needs the consumer and its `rate_limit_per_min` (from the snapshot), and requests refused with 401/403 cost no budget. Public routes (`auth_required = false`) have no consumer; since MT they are limited by the workspace bucket. A per-IP limit for them stays a v2 idea.
- Allowed: `X-RateLimit-Limit` and `X-RateLimit-Remaining` on the response. Empty bucket: `429 {"error":"rate_limit_exceeded"}` with the same headers and `Retry-After` in whole seconds (rounded up, at least 1).
- Fail open: if Redis doesn't answer within 50 ms, the request goes through without rate-limit headers. The warning is throttled to one line per 10 s, with a count of the held-back ones, so an outage can't flood the log. After Redis comes back, limiting resumes within about 3 s (go-redis retries dialing in the background instead of on every request).
- The request log's `ratelimit` field is `""` (not limited), `"ok"`, `"limited"` (429 by the consumer bucket), `"limited_workspace"` (429 by the workspace bucket, MT) or `"unavailable"` (Redis down, let through). The client gets the same `429 rate_limit_exceeded` either way.
- The Redis client connects lazily (Redis down at startup is fine), has 50 ms read/write/pool timeouts and no retries.

**Error responses**

| Status | When | Body |
| --- | --- | --- |
| 401 | No `X-API-Key` header (with `WWW-Authenticate`) | `{"error":"missing_api_key"}` |
| 401 | Key unknown or revoked (same answer for both), or sent twice | `{"error":"invalid_api_key"}` |
| 403 | Consumer blocked | `{"error":"consumer_blocked"}` |
| 403 | Workspace suspended (MT) | `{"error":"workspace_suspended"}` |
| 404 | Unknown host or workspace not in the config (MT) | `{"error":"workspace_not_found"}` |
| 413 | Body over the route's or the platform's limit (MT) | `{"error":"request_too_large"}` |
| 503 | Workspace's in-flight limit reached (MT); `Retry-After: 1` | `{"error":"workspace_overloaded"}` |
| 503 | No healthy target for the route's service (MT) | `{"error":"no_healthy_upstream"}` |
| 502 | Target's IP blocked by the SSRF policy (MT) | `{"error":"upstream_not_allowed"}` |
| 400 | Path with an encoded `.` or `..` segment (`%2e%2e`) | `{"error":"invalid_path"}` |
| 404 | No route matches | `{"error":"route_not_found"}` |
| 405 | A route's path fits but none allows the method; `Allow` lists the methods | `{"error":"method_not_allowed"}` |
| 503 | No config loaded yet (gateway just started); `Retry-After: 1` | `{"error":"config_not_loaded"}` |
| 429 | Rate limit hit (with `Retry-After` and `X-RateLimit-*`) | `{"error":"rate_limit_exceeded"}` |
| 502 | Upstream refused or reset the connection | `{"error":"bad_gateway"}` |
| 504 | Upstream exceeded the route timeout | `{"error":"gateway_timeout"}` |

**Log line**

```json
{"ts":"2026-11-15T16:00:00Z","level":"INFO","msg":"request","request_id":"a1b2","workspace_id":"ws_acme","method":"GET","path":"/v1/orders/7","route_id":"r1","target_id":"t1","consumer_id":"c1","key_id":"k1","ratelimit":"ok","status":200,"latency_ms":12.4,"upstream_ms":9.1,"gateway_ms":3.3,"client_ip":"172.18.0.1","bytes":245}
```

Logs use Go's `log/slog` with the JSON handler. The API key itself never appears in a log, and neither does the query string (it can hold tokens or personal data). Times are milliseconds with microsecond precision. `upstream_ms` is the wait until the backend's response headers; `gateway_ms` = `latency_ms` − `upstream_ms` is the time the gateway added. `status` 499 means the client hung up; `aborted: true` marks an answer cut off mid-stream. `GET /health` is not logged. A panic adds an `ERROR` line with the stack trace and the request ID. Since MT every line carries `workspace_id` (empty only when the host matched no workspace), and the control plane's own log lines carry it too.

## 8. Frontend

**The frontend is built last, all at once** (decided Nov 20, 2026): after MT and after the v2 and v3 backend work, Claude builds the whole React app in one milestone (FE) against the final control-plane API and its OpenAPI spec. Until then the backend is driven through Swagger, scripts and the e2e tests.

The app has three areas, each for a different kind of user. "Admin" is not used as a name for the whole app: most of its users are workspace members (Owner, API Admin, Viewer), and the Platform Admin has its own area.

| Area | Path | For whom |
| --- | --- | --- |
| **Dashboard** | `/w/{slug}/...` | Members of a workspace; what they see and can change depends on their role |
| **Platform** | `/platform/...` | The Platform Admin only (MFA required) |
| **Developer portal** | `/portal/...` | External developers who consume the APIs (v3) |

Plus the shared account pages: login (GoTrue, with MFA), account and MFA setup, and the workspace switcher.

**Account (shared)**

| # | Page | Path | Contains | Data from |
| --- | --- | --- | --- | --- |
| 1 | Login | `/login` | Email and password, then the MFA code when enabled; password recovery link | MT |
| 2 | Account | `/account` | Email, MFA setup (TOTP QR code), sign out of all devices | MT |
| 3 | Workspaces | `/` | My workspaces and my role in each; opens the dashboard of one | MT |

**Dashboard (workspace members)**

| # | Page | Path | Contains | Data from |
| --- | --- | --- | --- | --- |
| 4 | Overview | `/w/{slug}` | Stat cards (requests, error rate, p95, active consumers), 24-hour traffic chart, top routes and consumers, recent errors | v2 |
| 5 | Services | `/w/{slug}/services` | Table, add and edit modal, delete | MVP |
| 6 | Service detail | `/w/{slug}/services/:id` | Editable info, targets with weights and health, health-check settings, its routes, traffic chart | MT, v2 |
| 7 | Routes | `/w/{slug}/routes` | Table, add modal with methods, service, auth toggle, CORS and body limit | MVP, MT |
| 8 | Consumers | `/w/{slug}/consumers` | Table with status and plan, add, block and unblock | MVP |
| 9 | Consumer detail | `/w/{slug}/consumers/:id` | Info, API keys list, generate key (shown once), revoke, usage chart | MVP, v2 |
| 10 | Members | `/w/{slug}/members` | Members and roles, add by email, change role, remove; warning when there is only one owner | MT |
| 11 | Activity | `/w/{slug}/activity` | Audit log with filters; a row opens the before/after details | MT |
| 12 | Settings | `/w/{slug}/settings` | Name, limits and quotas (read-only for members), delete workspace (Owner, type the slug to confirm) | MT |
| 13 | Plans | `/w/{slug}/plans` | Plan cards, create and edit modal | v2 |
| 14 | Logs | `/w/{slug}/logs` | Filters, request table, detail drawer | v2 |

A Viewer sees every dashboard page read-only; buttons a role can't use are hidden.

**Platform (Platform Admin)**

| # | Page | Path | Contains | Data from |
| --- | --- | --- | --- | --- |
| 15 | Workspaces | `/platform/workspaces` | All workspaces with status; create (with first owner), suspend, unsuspend, restore, change limits | MT |
| 16 | Users | `/platform/users` | All users, their workspaces, disable and enable | MT |
| 17 | Platform activity | `/platform/activity` | Audit events across all workspaces | MT |

**Developer portal (v3)**

| # | Page | Path | Contains | Data from |
| --- | --- | --- | --- | --- |
| 18 | Sign up and login | `/portal/login` | Developer account forms | v3 |
| 19 | API catalog | `/portal/apis` | Cards for published APIs | v3 |
| 20 | API docs | `/portal/apis/:id` | Rendered OpenAPI docs, try-it console | v3 |
| 21 | My keys | `/portal/keys` | Create, list, revoke own keys | v3 |
| 22 | My usage | `/portal/usage` | Requests over time, quota bar, plan | v3 |

"Data from" names the backend milestone that provides a page's data; every page is built in the FE milestone.

**Shared layout**

Dashboard sidebar (Overview, Services, Routes, Consumers, Members, Activity, Plans, Logs, Settings); top bar with the workspace switcher, the user's name and role, and sign out; a Platform link for the Platform Admin; light and dark theme. In development Vite proxies `/api` to the control plane, so the app and the API share one origin (no CORS needed for the app). The session is handled by `supabase-js` (see MT_ROLES_DECISIONS.md decision 4 for its storage trade-off).

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
│   │       ├── config/           # poller and snapshot store (per-workspace merge)
│   │       ├── tenant/           # host → workspace (MT)
│   │       ├── middleware/       # recover, requestid, logger, tenant, cors, bodylimit, auth, ratelimit, concurrency
│   │       ├── proxy/            # route matching and reverse proxy
│   │       ├── upstream/         # targets, weighted round robin, active and passive health checks (MT)
│   │       ├── egress/           # SSRF policy and the guarded dialer (MT)
│   │       ├── ratelimit/        # Redis client and Lua script (consumer + workspace buckets)
│   │       ├── reqctx/           # per-request facts the log needs (request ID, workspace, route, target, times)
│   │       ├── server/           # http.Server, timeouts, /health, graceful shutdown
│   │       ├── respond/          # the gateway's own JSON answers, {"error": "..."}
│   │       └── settings/         # env vars, validated at startup
│   ├── control-plane/            # FastAPI, organized by feature
│   │   ├── app/
│   │   │   ├── core/             # settings, db session, GoTrue JWT verification, permissions, shared deps
│   │   │   ├── users/            # users, just-in-time creation, /me (replaces auth/ and admins)
│   │   │   ├── workspaces/       # workspaces, lifecycle, limits, purge job
│   │   │   ├── members/          # memberships, owner rules, GoTrue invitations
│   │   │   ├── services/         # services and their targets
│   │   │   ├── routes/           # router.py, models.py, schemas.py, crud.py
│   │   │   ├── consumers/        # same four files
│   │   │   ├── keys/             # same four files
│   │   │   ├── audit/            # audit_events, record(), the audit endpoints, retention job
│   │   │   ├── gateway_config/   # the /internal/config endpoint (incremental)
│   │   │   ├── gotrue/           # thin client for GoTrue's admin API (invite, ban, create user)
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
| `postgres` | 5432 (host 5434) | Databases `apim` (ours) and `auth` (GoTrue's). Mapped to 5434 on the host because local Postgres installs already use 5432 and 5433 |
| `auth-db-init` | — | One-shot job before GoTrue starts (MT): creates GoTrue's login (`gotrue`), the `auth` database and schema, and the `postgres` role GoTrue's migrations expect; safe to repeat |
| `auth` | 9999 | GoTrue (MT, pinned `v2.197.0`): login, MFA, refresh, invitations, recovery |
| `control-plane-db-init` | — | One-shot job that prepares the Postgres logins and runs migrations before the API starts |
| `audit-retention` | — | Deletes audit events older than `AUDIT_RETENTION_DAYS`, at start and once a day, as `apim_retention` (MT.3); gets only that login and the period |
| `mailpit` | 1025 (SMTP), 8025 (UI, bound to 127.0.0.1) | Catches every email locally and in CI (MT); its inbox holds invitation and reset links, so it isn't reachable from the network |
| `redis` | 6379 | Rate-limit state |
| `control-plane` | 8000 | Admin API and internal config endpoint |
| `gateway` | 8080 (host 8081) | Public entry point. Mapped to 8081 on the host because another local app already uses 127.0.0.1:8080 |
| `mock-backend` | 9000 | Echoes requests so there is something to proxy to; `?delay_ms=N` answers late, to see a `504` |
| `frontend` | 5173 | The React app: dashboard, platform and portal (FE milestone) |

**Environment variables**

| Service | Variable | Example |
| --- | --- | --- |
| Control plane | `DATABASE_URL` | `postgresql+asyncpg://apim_app:<password>@postgres:5432/apim` (the API's own login, MT.3) |
| control-plane-db-init | `MIGRATION_DATABASE_URL` | `postgresql+asyncpg://app:app@postgres:5432/apim` (the owner for migrations and setup) |
| control-plane-db-init, audit-retention | `AUDIT_RETENTION_DATABASE_URL` | `postgresql+asyncpg://apim_retention:<password>@postgres:5432/apim` |
| Control plane | `INTERNAL_TOKEN` | shared secret with the gateway |
| Control plane | `GOTRUE_URL` | `http://auth:9999` |
| Control plane | `GOTRUE_JWT_SECRET` | the secret GoTrue signs with, to verify its tokens (same value as GoTrue's) |
| Control plane | `GOTRUE_SERVICE_ROLE_KEY` | token for GoTrue's admin API (invite, ban, create user); a secret |
| Control plane | `PLATFORM_ADMIN_EMAIL` | the first Platform Admin. At startup, a row without a GoTrue id is linked to the account found through the admin API, which is created or invited only if missing (a pending invitation is never resent). A row already bound stays bound: if GoTrue now gives the email another account (or none), startup keeps the binding and privileges, creates nothing and logs an `ERROR` with both ids |
| Control plane | `PLATFORM_ADMIN_PASSWORD` | optional: that account's first password (12+ characters); empty → GoTrue emails an invitation instead. Never overwrites an existing account's password |
| Control plane | `UPSTREAM_ALLOW` | `mock-backend` locally and in CI; empty in production |
| Control plane | `WORKSPACE_RATE_LIMIT_PER_MIN`, `WORKSPACE_MAX_CONCURRENT` | defaults `6000`, `200` |
| Control plane | `WORKSPACE_MAX_SERVICES`, `_ROUTES`, `_CONSUMERS` | defaults `100`, `500`, `5000` |
| Control plane | `MAX_REQUEST_BODY`, `MAX_REQUEST_BODY_CEILING` | defaults `10MB`, `100MB` |
| Control plane | `PURGE_AFTER_DAYS`, `AUDIT_RETENTION_DAYS` | defaults `30`, `365` |
| GoTrue | `GOTRUE_DB_DATABASE_URL` | the `auth` database, as its own login `gotrue` (password `GOTRUE_DB_PASSWORD`), which can't open `apim` |
| GoTrue | `GOTRUE_JWT_SECRET`, `GOTRUE_JWT_EXP` | shared with the control plane; `600` (10 minutes) |
| GoTrue | `GOTRUE_DISABLE_SIGNUP` | `true`: joining is only by being added to a workspace |
| GoTrue | `GOTRUE_PASSWORD_MIN_LENGTH` | `12` |
| GoTrue | `GOTRUE_SITE_URL`, `GOTRUE_SMTP_*` | the dashboard URL; Mailpit locally |
| GoTrue | rate-limit settings | `GOTRUE_RATE_LIMIT_TOKEN_REFRESH=30` (calls to `/token` per 5 minutes per IP; default 150), `GOTRUE_MFA_RATE_LIMIT_CHALLENGE_AND_VERIFY=10` (per minute; default 15) |
| Gateway | `CONTROL_PLANE_URL` | `http://control-plane:8000` (required) |
| Gateway | `INTERNAL_TOKEN` | same shared secret (required; never logged) |
| Gateway | `REDIS_URL` | `redis://redis:6379/0` (required; `rediss://` for TLS; treated as a secret, since it may hold a password) |
| Gateway | `CONFIG_WAIT` | `30s` (how long the control plane may hold a config request; whole seconds, 1 to 60) |
| Gateway | `LISTEN_ADDR` | `:8080` (set in `docker-compose.yml`, next to the port mapping that depends on it) |
| Gateway | `SHUTDOWN_TIMEOUT` | optional, default `5s` (time given to in-flight requests on stop) |
| Gateway | `GATEWAY_BASE_DOMAIN` | `localhost` locally; e.g. `gw.example.com` in production (MT) |
| Gateway | `UPSTREAM_ALLOW` | same value as the control plane's (MT) |

The MVP's `JWT_SECRET`, `JWT_EXPIRE_MINUTES`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` are removed in MT. The gateway container gets only the variables it uses, not the whole `.env`: it never needs the database password, the GoTrue secrets or the service-role key.

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
| MT: Multi-tenancy and login | after M4 | GoTrue login, workspaces, roles, audit, per-workspace gateway, load balancing, SSRF, CORS, body limit | Ali with an AI agent |
| v2: Plans and analytics (backend) | after MT | Plans and quotas, usage events, analytics endpoints, Prometheus metrics | Ali with an AI agent |
| v3: Portal and extras (backend) | after v2 | Developer accounts and portal API, JWT auth, transforms, caching, LLM gateway mode | Ali with an AI agent |
| FE: Frontend | **last** | The whole React app at once: dashboard, platform pages and developer portal (section 8) | Claude |

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
- [x] README with setup steps and an architecture picture (developer commands in docs/DEVELOPMENT.md; `scripts/init-env` creates `.env`)

**MT: Multi-tenancy and login (after the MVP)**

Each step is one self-contained piece of work that an AI agent finishes completely (code, tests, docs, logical commits) before handing back. Outcomes and done criteria for each step are in ROADMAP.md; the reasons are in MT_ROLES_DECISIONS.md (decision numbers in brackets).

- [x] MT.1 Identity: GoTrue and Mailpit, `users` replaces `admins`, JWT verification, Platform Admin with MFA, disabling users [4, 12, 13]
- [x] MT.2 Workspaces and roles: memberships, permissions, URLs under `/workspaces/{slug}`, isolation tests [0, 2, 5, 13]
- [x] MT.3 Audit log: same-transaction records for every write, guard test, audit endpoints, retention [11]
- [x] MT.4 Members and owners: add (GoTrue invite), change role, remove, leave, last-owner rules [8, 9]
- [ ] MT.5 Workspace lifecycle and quotas: create, suspend, delete, restore, purge; limit columns [6, 10]
- [ ] MT.6 Incremental config: `config_seq`, per-workspace versions, gateway merge [7]
- [ ] MT.7 Gateway tenancy: workspace from the Host, key and status checks, `X-Workspace-ID`, `workspace_id` in logs [1, 10, 17]
- [ ] MT.8 Workspace limits in the gateway: workspace bucket, in-flight limit [6]
- [ ] MT.9 SSRF protection: guarded dialer, allow list, early check on save [3]
- [ ] MT.10 Load balancing: targets, weighted round robin, active and passive health checks [14]
- [ ] MT.11 CORS and body size limit [15, 16]
- [ ] MT.12 MT end to end: e2e stories, load test, README and developer docs [all]

**v2 and v3 (backend):** planned in detail after MT.

**FE: Frontend (Claude, last)**

- [ ] FE.1 Foundation: Vite project, layout, GoTrue login and MFA, workspace switcher, role-aware routing, generated API types
- [ ] FE.2 Dashboard pages (section 8, pages 4–14)
- [ ] FE.3 Platform pages (pages 15–17) and account pages (pages 1–3)
- [ ] FE.4 Developer portal (pages 18–22)

## 11. Testing and observability

Each service has its own tests, and one end-to-end script proves the whole stack works together.

| Level | Tool | Covers |
| --- | --- | --- |
| Control plane unit and API tests | pytest, httpx, a test Postgres database | Every endpoint, key hashing, config snapshot. Since MT tests sign in with tokens signed by the test GoTrue secret (no GoTrue process needed in unit tests) |
| Permission guard tests (MT) | pytest, discovering every endpoint from the app | Every endpoint declares a permission; every role gets exactly the answers the permission table promises (403 / 404) |
| Isolation tests (MT) | pytest, discovering every endpoint | A member of workspace A can never read, list, change or reference any object of workspace B (always 404 or 422) |
| Audit guard test (MT) | pytest, discovering every mutating endpoint | Every POST, PATCH and DELETE writes exactly one audit event in the same transaction, with no secrets in it |
| Gateway unit tests | `go test`, `httptest` | Route matching, auth, error mapping, middleware order; the whole program start to stop against a fake control plane; requests under load while the config changes (`-race`); coverage at least 90% |
| Gateway fuzz tests | `go test -fuzz` | Path checks, matching, prefix stripping and request IDs against independent reference rules; since MT also host parsing and the SSRF IP policy; 10 s each in CI |
| Balancer and health-check tests (MT) | `go test` with fake targets (`httptest`) | Weighted distribution over many picks, unhealthy targets skipped, recovery, passive ejection and return, all-down 503 |
| Gateway benchmarks | `go test -bench` (by hand) | Matching vs. number of routes, key lookup vs. number of keys, the whole chain without network |
| Rate limiter tests | `go test` against a real Redis (Docker locally, database 15; a Redis service in CI); skipped without `REDIS_TEST_URL` | Burst, refill, `retry_after`, separate buckets, lowered limit, expiry, script reload, 100 concurrent requests for 50 tokens |
| End to end | pytest + httpx in `e2e/`, run in its own container (`docker compose run --rm e2e`) and in CI on a fresh stack | The MVP story: admin API creates service, routes, consumers and keys, then calls through the gateway: 200 (backend sees `X-Consumer-ID`, not the key), 401, 403 after a block, 401 after a revoke, a new route live within 1 s, 404, 405, 429, 502, 504, `strip_prefix`, public routes. Own objects, always cleaned up. **MT adds:** GoTrue login (and MFA for the Platform Admin), two workspaces with the same path reaching different backends, a key used on the wrong workspace's host (401), roles (a Viewer can't write), last-owner rules, suspend (403) and restore, audit entries for each change, the workspace rate limit, SSRF (`upstream_not_allowed`), traffic spread over two targets and moved off a stopped one, CORS preflight, 413. Invitation emails are read from Mailpit's API |
| Load | k6 in Docker (`docker compose run --rm loadtest`); a 5 s smoke run in CI | Three runs at the same fixed rate: direct to the backend, through the gateway on a public route, and on a protected route (key + Redis). Measured at 300 requests/s: the gateway adds 0.77 ms median and 0.95 ms p95 in total; its own code about 0.08 ms, the Redis rate limit about 0.3 ms, the rest is the extra network hop. 0 errors |

**Observability**

- **MVP:** structured JSON logs from both services, with the request ID carried from the gateway to the backend.
- **MT:** `workspace_id` on every log line of both services; admin changes in the audit log; login events in GoTrue's own log.
- **v2:** Prometheus metrics from the gateway (`requests_total` by workspace, route and status, a latency histogram, `ratelimited_total`, target health, config sequence and last sync time) and a Grafana dashboard.
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
| A forgotten `workspace_id` filter leaks data between workspaces (MT) | Every `crud.py` function takes `workspace_id` first; automatic isolation tests over every endpoint; composite foreign keys. RLS stays possible later |
| A tenant's backend URL reaches internal services (SSRF) (MT) | IP check in the dialer at connect time, deny by default, allow list set by the Platform Admin only |
| One workspace slows everyone down (MT) | Per-workspace rate limit, in-flight limit and quotas |
| The Platform Admin account is compromised (MT) | MFA required on every platform endpoint; every action audited and visible to the workspace's owners |
| GoTrue becomes a dependency to run and update (MT) | Pinned image version, own database, no public sign-up; gateway traffic never depends on it |
| A workspace ends up with no owner (MT) | Last active human owner can't be demoted or removed; row lock against races; Platform Admin can assign one |

**Open questions**

- [ ] Should the rate limit live on the consumer (MVP) or move to plans from day one?
- [x] One admin account, or several admins with roles? *One seeded admin in the MVP. MT: several members per workspace with fixed roles (Owner, API Admin, Viewer) plus a Platform Admin; see MT_ROLES_DECISIONS.md.*
- [ ] Where will the demo run: local only, or a small cloud VM?
- [ ] Should usage events go from the gateway over HTTP batches or a Redis stream (v2)?

**Patterns to apply later (reminder)**

Config sync doesn't need these: the change and the sequence bump share one transaction, and the gateway receives each changed workspace complete, which is naturally idempotent. The MT audit log doesn't need them either: the audit row is written in the same transaction and the same database as the change. Later milestones do need them:

| When | Pattern | What it solves |
| --- | --- | --- |
| v2 usage events (gateway → control plane batches) | **Inbox** | Each batch carries an id; the control plane records processed ids and skips repeats, so a retried batch is never counted twice |
| v3 webhooks (e.g. tell a customer "key revoked") | **Outbox** | The change and an outbox row are written in one transaction; a worker delivers with retries, so a webhook is never lost or sent for a change that rolled back |
| Shipping audit events to an external system (later) | **Outbox** (same idea) | Export to a SIEM or log store without losing events or sending rolled-back ones |
