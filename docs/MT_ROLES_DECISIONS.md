# Multi-tenancy and roles: design decisions

Working log of the decisions for multi-tenancy (MT) and roles, made before writing the architecture and the plan. Started Nov 20, 2026.

**Status:** 17 decided (15 and 16: feature accepted, details proposed, to confirm with the architecture). Next: write the architecture and the plan.

**Order of work:** all backend work first (MT and login, then the v2 and v3 backend); **the whole frontend comes last, all at once** (milestone FE). Its areas are named Dashboard (workspace members), Platform (Platform Admin) and Developer portal; the page list is in PLAN.md section 8.

| # | Topic | Status |
| --- | --- | --- |
| 0 | Role names | ✅ Decided |
| 1 | How the gateway finds the tenant | ✅ Decided |
| 2 | Workspace in the admin API, role checks | ✅ Decided |
| 3 | SSRF protection | ✅ Decided |
| 4 | Authentication (GoTrue) | ✅ Decided |
| 5 | Tenant isolation in the database | ✅ Decided (RLS deferred) |
| 6 | Noisy neighbor | ✅ Decided |
| 7 | Config version per workspace | ✅ Decided |
| 8 | Invitations and email | ✅ Decided (simple option) |
| 9 | Owner rules | ✅ Decided |
| 10 | Workspace deletion and suspension | ✅ Decided |
| 11 | Audit log | ✅ Decided |
| 12 | Login | ✅ Decided |
| 13 | Moving the running system to MT | ✅ Decided |
| 14 | Load balancing and health checks | ✅ Decided |
| 15 | CORS per route | ✅ Accepted (details proposed) |
| 16 | Request body size limit | ✅ Accepted (details proposed) |
| 17 | `workspace_id` in logs and metrics | ✅ Decided |

---

## ✅ Decided

### 0. Role names

| Role | In code | Scope | Can do |
| --- | --- | --- | --- |
| Platform Admin | `users.is_platform_admin` | Whole platform | Create and manage workspaces; recovery (assign owners) |
| Owner | `owner` | One workspace | Everything in the workspace, plus members and roles |
| API Admin | `api_admin` | One workspace | Services, routes, consumers, keys, plans |
| Viewer | `viewer` | One workspace | Read-only: config, logs, analytics |
| Developer | `developers` table (v3) | Developer portal | External; own apps, keys and usage |

Machine identities (not roles): **Consumer** (authenticates with `X-API-Key` at the gateway) and **Gateway** (authenticates with `X-Internal-Token`).

- Fixed roles, ordered `owner > api_admin > viewer`. No custom roles for now.
- "Developer" is reserved for the external portal user, as in Apigee.
- Roles live on a membership (`workspace_members`), so one person can hold different roles in different workspaces.

### 1. How the gateway finds the tenant

**Decision:** from the **Host** header, one subdomain per workspace slug.

- `acme.gw.example.com/v1/orders` → slug `acme` → `workspace_id` → match only that workspace's routes. Two workspaces can both use `/v1/orders`.
- No subdomain (plain `localhost:8081`) → the `default` workspace, so existing tests keep working.
- Locally: `acme.localhost:8081` (no DNS, no certificates).
- After auth, the key's workspace must equal the Host's workspace, otherwise **401**.
- The gateway sets a trusted `X-Workspace-ID` for the backend and strips any client-sent copy.
- Snapshot holds `host → workspace_id` and `workspace_id → routes`, in memory (not Redis).
- Custom domains (`api.acme.com`) later through a `workspace_hosts` table, same mechanism.
- Production later: wildcard DNS record + wildcard TLS certificate; Caddy in front of the gateway for TLS and custom-domain certificates.

**Why:** the tenant must be known before route matching; public routes have no key; clean URLs for the tenant's clients; the same path leads to custom domains.
**Fallback:** if wildcard DNS isn't available, use a path prefix (`/acme/v1/orders`); the gateway mechanism is almost the same.

### 2. Workspace in the admin API, role checks

**Decision:** the workspace slug is in the **URL**; the token carries only the user; the role is read from the database on every request.

```
/api/v1/auth/...                          no workspace
/api/v1/me/workspaces                     my workspaces and my role in each
/api/v1/workspaces                        Platform Admin: create, list
/api/v1/workspaces/{slug}/services        all CRUD under the workspace
/api/v1/workspaces/{slug}/members         Owner: members and roles
/internal/config                          stays global (the gateway needs all)
```

Per request: verify the JWT (who) → resolve slug and membership in **one query** (which workspace, which role) → check the permission.

- Endpoints use **`require_permission("services:write")`**, not `require_role(...)` (idea taken from Ali's earlier Planora project). Role → permissions is a constant map in code for now; it can move to database tables later for custom roles without touching endpoints.
- Unknown workspace or not a member: **404** (does not reveal that it exists). Role too low: **403**.
- Slug is immutable after creation (it is also the subdomain).
- Old URLs (`/api/v1/services`) are removed without aliases; tests, e2e and `openapi.json` are updated. No frontend or external clients exist yet, so now is the cheapest time.

**Why:** explicit in URLs, logs and OpenAPI; several workspaces in several tabs; shareable links; natural for the Platform Admin; same slug as decision 1; removing a member or lowering a role takes effect on the very next request.

### 3. SSRF protection

**Decision:** block internal targets by default, checked at **connect time** in the gateway, plus an early check in the control plane.

1. **Gateway (the real protection):** a `net.Dialer` `Control` hook checks the resolved IP before connecting, so DNS rebinding can't bypass it. Blocked by default: `127.0.0.0/8`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`, `169.254.0.0/16`, `fe80::/10`, `0.0.0.0/8`, `100.64.0.0/10`, multicast. IPv4-mapped IPv6 is unmapped first. Answer: `502 {"error":"upstream_not_allowed"}` plus a warning log with the workspace.
2. **Control plane (a clear error early):** only `http` and `https`; a blocked IP literal or a known internal name (`localhost`, `*.internal`) → **422** when the service is saved. Convenience, not security.
3. **Exceptions set by the Platform Admin only:** `UPSTREAM_ALLOW=mock-backend,10.20.0.0/16` (gateway and control plane). Local Compose and CI allow `mock-backend`; production default is empty.

- Fuzz test for the IP policy; new e2e story: a service pointing at `http://redis:6379` gets 502 `upstream_not_allowed`.
- Cost is near zero: the check runs once per new connection, not per request.

### 4. Authentication

**Decision:** use **GoTrue (Supabase Auth), self-hosted**, for authentication. We build authorization (workspaces, roles, permissions). **No token exchange.**

- The dashboard logs in with GoTrue and calls the control plane with GoTrue's access token. The control plane verifies the JWT, then reads the role from its own database (decision 2).
- GoTrue handles login, refresh-token rotation with reuse detection, logout, sessions, password recovery and invitation emails.
- Our `users` table links to GoTrue with `auth_user_id`; `is_active` is checked on every request (a disabled user stops at once). Short access tokens (5–15 min).
- GoTrue gets its own database (`auth`) on the same Postgres server, so its migrations don't mix with Alembic.
- New Compose services: `auth` (GoTrue) and `mailpit` (catches emails locally, UI on `localhost:8025`).
- The current login and argon2 code leave the control plane; `admins` becomes `users` + `workspace_members`; tests and e2e log in through GoTrue.
- Known trade-off: `supabase-js` stores the session in `localStorage` by default. Accepted because access tokens are short and refresh tokens rotate; custom storage is possible later.

**Why:** most companies use a ready-made identity provider for authentication and build authorization themselves (the same split as Planora). GoTrue is light, runs on our Postgres, covers invites, recovery, MFA and SAML SSO, and Ali already knows it. Keycloak is stronger but too heavy for this project.

### 5. Tenant isolation in the database

**Decision:** isolation in **application code only** for now; Postgres Row-Level Security is deferred.

- All data access goes through each feature's `crud.py`; every function takes `workspace_id` as a required first parameter.
- Composite foreign keys on (`parent_id`, `workspace_id`) stay.
- **Automatic isolation tests:** like the existing guard tests, a test discovers every endpoint and checks that workspace A can never read or change workspace B's data.
- Every tenant table keeps `workspace_id`, so RLS can be added later without a data migration.

### 6. Noisy neighbor

**Decision:** the gateway stays **shared (pooled)** by all workspaces, with three layers of protection.

1. **Requests per minute per workspace (Redis):** the same Lua script checks the consumer bucket and the workspace bucket (`rlws:{workspace_id}`) in one round trip and spends only if both allow. Also limits public routes, which had no limit. Default from `WORKSPACE_RATE_LIMIT_PER_MIN` (e.g. 6000); the Platform Admin can change it per workspace. Fail open as today.
2. **Concurrent requests per workspace (in memory):** a semaphore per gateway instance (e.g. 200), so a slow backend can't hold more than its share. Over the limit: `503 {"error":"workspace_overloaded"}` with `Retry-After: 1`. The limit is per instance on purpose.
3. **Config size per workspace (control plane):** defaults of 100 services, 500 routes, 5,000 consumers (10 active keys per consumer already exists). Over the limit: `409 quota_exceeded`. Adjustable per workspace by the Platform Admin.

- `workspaces` gets nullable `rate_limit_per_min`, `max_concurrent`, `max_services`, `max_routes`, `max_consumers` (NULL = default). Limits travel in the snapshot.
- Log field `ratelimit` gains `limited_workspace`. Clients get the same `429 rate_limit_exceeded`.
- Note for later: with Redis Cluster both keys of one script must share a hash slot.

**Not chosen:** fair queuing (too complex), priority by plan (comes with plans in v2).

### 7. Config version per workspace

**Problem:** today there is one snapshot with one `config_version` for everything. With many workspaces, any small change in one workspace makes every gateway download and rebuild the configuration of all workspaces (e.g. ~10 MB for 100 workspaces instead of ~100 KB for the one that changed; rough estimate).

**Decision (B):** a **version per workspace** plus a **global sequence**; the gateway receives only the workspaces that changed.

| Number | Purpose |
| --- | --- |
| `version` per workspace | Which version of that workspace's config a gateway holds (cache key, logs, debugging) |
| `config_seq` (global) | The single number the gateway remembers, to ask "what changed after N?" and to keep changes in order |

```
config_seq = 1046
workspace_config(workspace_id, version, seq)
  acme     version 18  seq 1046
  globex   version 9   seq 1031
```

- **Control plane:** the triggers find the `workspace_id` of the changed row, bump `config_seq`, and set that workspace's `version += 1` and `seq = config_seq`. `NOTIFY config_changed` carries the workspace id. `config_seq` replaces the global `config_version`.
- **Request:** `GET /internal/config?since=<seq>&wait=30` → `{seq, full, workspaces: [changed ones, each complete], deleted: [...]}`, found with `WHERE seq > :since`. No change → long poll as today (`NOTIFY` or 30 s, then `304`). `since` replaces `If-None-Match`; the `ETag` is `seq`.
- **Gateway:** merges instead of replacing: builds a new snapshot that reuses the unchanged workspaces (same pointers), puts in the changed ones, removes the deleted ones, then swaps it with `atomic.Pointer` (no locks).
- **Full resync:** `since=0` (gateway start), `since` greater than the current sequence (e.g. after a database reset), or anything unexpected → `full: true` with every workspace. The control plane stays the source of truth.
- Several quick changes to one workspace arrive once, as its latest state. A lost `NOTIFY` only delays an update until the next poll.
- `REPEATABLE READ` snapshot reads and the per-version cache (now per `(workspace_id, version)`) stay.
- `?workspaces=acme` filtering supports a dedicated gateway per large workspace later.

**Why now:** decision 1 already changes the snapshot format (`host → workspace`, `workspace → routes`), so the protocol changes once instead of twice.
**Not chosen:** (A) keep one global version and the full snapshot (simple, but every change costs every workspace); (C) a row-level change stream (too complex).

### 8. How people join a workspace

**Decision: the simple option.** An Owner adds a person by email and role; there is no accept/decline step.

There is **no public sign-up** (`GOTRUE_DISABLE_SIGNUP=true`): the only way to get an account is to be added to a workspace. So "has an account" means someone added this person to some workspace before and they set a password then.

`POST /api/v1/workspaces/{slug}/members {email, role}` (Owner):

- **New person (no account):** GoTrue `/invite` emails them → they set a password and log in → they are a member.
- **Existing person (already a member of another workspace):** the membership is created **immediately**, with no email and no question; the workspace appears in their list at the next login.

- Inviting someone who is already a member → `409`.
- The first Platform Admin is created at startup through the GoTrue admin API (replaces seeding from `ADMIN_EMAIL`).
- **Just-in-time users:** the first request with a new GoTrue JWT creates the `users` row from its claims; no sync job between GoTrue and our database.
- **Email:** Mailpit locally and in CI (UI on `localhost:8025`); any SMTP in production. Email templates are configured in GoTrue.
- **Password recovery:** the dashboard calls GoTrue `/recover` directly.
- Audit actions: `member.add`.

**Known trade-off:** an Owner can add an existing user to their workspace without that user's consent. Acceptable for this project.
**Not chosen now:** the full flow (`workspace_invitations` table, accept/decline, 7-day expiry, cancel), about a week of work. It can be added later without changing the simple flow: the add-member endpoint would create an invitation instead of a membership.
**v3 note:** portal developers need open sign-up, probably through a second GoTrue instance.

### 9. Owner rules

**Decision:** the GitHub / GitLab model: several owners, never zero, plus lessons from GitLab's bugs.

1. **Several owners allowed.** Any owner can change the role of any other member, including other owners, but **not their own role** (as on GitHub; prevents demoting yourself by mistake). Ownership transfer: promote someone to owner, then they demote you, or you leave.
2. **At least one active human owner, always.** Demoting, removing, or the last owner leaving → `409 last_owner`. Only users with `is_active = true` count; future service accounts never count as owners. (GitLab had real bugs where a deactivated user or a bot ended up as the last owner and left groups unmanageable.)
3. **Concurrency:** `SELECT ... FOR UPDATE` on the workspace's owner rows before any role change or removal, so two owners can't demote each other at the same moment (same technique as the 10-keys limit).
4. **Platform Admin is the safety net:** can assign a new owner in any workspace (recorded in the audit log, visible to the workspace's owners), but follows rule 2 too: to remove the last owner, assign a new one first.
5. **Warning, not a block:** the dashboard shows "This workspace has only one owner; we recommend adding a second" (GitHub recommends at least two owners).

| Action | Owner | API Admin | Viewer |
| --- | --- | --- | --- |
| See members and roles | ✅ | ✅ | ✅ |
| Change roles, remove members | ✅ (not own role) | ❌ | ❌ |
| Leave the workspace | ✅ (unless last owner) | ✅ | ✅ |

Endpoints:

| Endpoint | Who |
| --- | --- |
| `POST /api/v1/workspaces` `{name, slug, owner_email}` | Platform Admin: create a workspace with its first owner |
| `GET /api/v1/workspaces/{slug}/members` | All members |
| `PATCH /api/v1/workspaces/{slug}/members/{user_id}` `{role}` | Owner |
| `DELETE /api/v1/workspaces/{slug}/members/{user_id}` | Owner |
| `DELETE /api/v1/me/workspaces/{slug}` | Any member (leave) |

- Adding members (`POST /members`): see decision 8.
- Audit actions: `member.role_change`, `member.remove`, `member.leave`, `member.owner_assigned_by_platform`.

### 10. Workspace deletion and suspension

**Decision:** a status on each workspace: suspend is reversible, delete is a 30-day soft delete, then a purge. Slugs are never reused.

| Status | Set by | Reversible |
| --- | --- | --- |
| `active` | default | — |
| `suspended` | Platform Admin (abuse, later non-payment) | ✅ unsuspend |
| `deleted` | Owner (permission `workspace:delete`) or Platform Admin | ✅ restore within 30 days (Platform Admin) |
| `purged` | daily job, 30 days after delete | ❌ |

`workspaces` gets `status` (text + CHECK, like `consumers.status`), `status_reason`, `deleted_at`, `purge_after`.

- **Suspended:** the gateway answers `403 {"error":"workspace_suspended"}` for all of the workspace's traffic, right after resolving the Host and before routing (the status travels in the snapshot). The admin API is read-only for members, so they can still see their config; any write → `403 workspace_suspended`.
- **Deleted:** the workspace leaves the snapshot (its Host → `404`) and disappears for members (`404`). Deleting requires typing the slug: `DELETE /api/v1/workspaces/{slug}` with `{"confirm": "acme"}`, as on GitHub.
- **Purged:** a daily job deletes all workspace data: services, routes, consumers, keys, memberships and the workspace's own audit rows. The `workspaces` row stays with status `purged`, so **the slug is never reused** (it is the subdomain; reuse would send a former tenant's clients to a new one). Users are not deleted (they stay in GoTrue and `users`, without this membership). Redis needs no cleanup: rate-limit keys expire within 2 minutes.
- **Audit:** lifecycle events (`workspace.created`, `suspended`, `unsuspended`, `deleted`, `restored`, `purged`) are written with `workspace_id = NULL` and `target_id = <workspace>`, so they are platform-level and survive the purge.
- **`default` is protected:** it can't be suspended or deleted (`409`), because requests without a subdomain go to it.

Endpoints:

| Endpoint | Who |
| --- | --- |
| `POST /api/v1/workspaces/{slug}/suspend` `{reason}` | Platform Admin |
| `POST /api/v1/workspaces/{slug}/unsuspend` | Platform Admin |
| `DELETE /api/v1/workspaces/{slug}` `{confirm}` | Owner or Platform Admin |
| `POST /api/v1/workspaces/{slug}/restore` | Platform Admin, within 30 days |

**Deferred:** exporting a workspace's configuration (JSON) before deletion.

### 11. Audit log

**Decision:** the application writes an audit row **in the same transaction** as each change, and a test makes forgetting impossible. Moved from v3 to **MT**, because MT is when several users share a workspace.

- **Purpose:** answer "who changed what, and when" (a route edited, a key revoked, a member promoted). Separate from the gateway's request logs, which record consumer traffic.
- **Same transaction:** the change and its audit row commit or fail together. No outbox needed, because both live in the same database. An outbox is only needed if events are later shipped to an external system.
- **Guard test:** like the existing guard tests, it discovers every mutating endpoint (POST, PATCH, DELETE) and fails if one writes no audit row.

```
audit_events
  id, created_at
  workspace_id        NULL = platform-level event (e.g. workspace created)
  actor_user_id, actor_email, actor_type    user | platform_admin | system
  action              service.create, key.revoke, member.role_change, ...
  target_type, target_id, target_name
  changes             jsonb, changed fields only: {"timeout_ms": [5000, 10000]}
  request_id, ip, user_agent
```

- `actor_email` and `target_name` are copied, so entries stay readable after the user or target is deleted.
- **No secrets ever:** a key is recorded by its prefix only; no `key_hash`, no tokens.
- **Append-only:** the application's database user has only `INSERT` and `SELECT` on `audit_events`. Only a retention job (default 1 year), running as a different database user, deletes old rows.
- **Platform Admin actions are recorded too** (`actor_type = platform_admin`) and the workspace's Owner sees them in the workspace's log. This closes the open gap about logging Platform Admin access.
- **Read:** `GET /workspaces/{slug}/audit-events` (Owner and API Admin, permission `audit:read`); `GET /api/v1/audit-events` (Platform Admin, all workspaces). Filters: actor, action, target, date range; newest first.
- **Login and logout events** stay in GoTrue's own table (`auth.audit_log_entries`); not duplicated.
- **Dashboard:** a new **Activity** page (`/admin/activity`): table with filters; a row opens the before/after details.

**Not chosen:** (B) Postgres triggers (they can't tell who made the change or what it means: "key generated" becomes "INSERT on api_keys"); (C) a separate log service with an outbox (only needed for shipping to an external system).

### 12. Login

**Decision:** dashboard users log in through GoTrue; MFA is required for the Platform Admin; GoTrue's brute-force limits are tightened.

**Who issues which identity:**

| Who | How they prove identity | Issued by |
| --- | --- | --- |
| Dashboard users (Platform Admin, Owner, API Admin, Viewer) | Email + password (+ MFA) → JWT | **GoTrue** |
| Consumers (tenants' client apps) | `X-API-Key` | Control plane (unchanged) |
| Gateway | `X-Internal-Token` | Shared secret in `.env` (unchanged) |
| Portal developers (v3) | Email + password → JWT | Probably a second GoTrue instance (open sign-up) |
| Service accounts (later) | Long-lived token for CI and automation | Control plane (to be decided then) |

- The control plane loses its login endpoint, argon2 and JWT **issuing**. It keeps JWT **verification** of GoTrue tokens (PyJWT), all API keys and the internal token. The gateway has nothing to do with GoTrue.

**Flow:** the dashboard's login page calls GoTrue `/token` through `supabase-js` (which also refreshes automatically) → access token + refresh token → the control plane verifies the JWT and reads the role from its database (decision 2). One error message for every failure: "invalid email or password", so it never reveals whether an email exists.

**Brute force:** GoTrue limits auth endpoints per IP address (token bucket). Weak points: the limit is per IP, not per account, and the default for password sign-ins is fairly high.

| Layer | Setting |
| --- | --- |
| GoTrue rate limits | Tightened for password sign-ins |
| Password policy | `GOTRUE_PASSWORD_MIN_LENGTH=12` (default 6) |
| MFA | The real protection: a guessed password is not enough |
| CAPTCHA | Later, if real attacks appear (GoTrue supports it) |

**MFA (TOTP, e.g. Google Authenticator):** the GoTrue JWT carries `aal` (`aal1` = password only, `aal2` = password + MFA).
- **Platform Admin: required.** Any platform endpoint without `aal2` → `403 mfa_required`. This account reaches every workspace, so it is the most dangerous one.
- Owner, API Admin, Viewer: optional for now. Later an Owner can require it for their whole workspace.

**Sessions:** access token 10 minutes (`GOTRUE_JWT_EXP=600`); refresh tokens rotate with reuse detection (GoTrue).

**Disabling a user (Platform Admin):** `users.is_active = false` → the next request returns `403 user_disabled`; the user is also banned in GoTrue (admin API), so they can neither refresh nor log in.

**Logout:** this device: `signOut()`; all devices: `signOut({ scope: 'global' })`, for when an account may be compromised.

**Audit:** login and logout events stay in GoTrue's own log (decision 11); disabling a user and MFA enforcement are recorded in `audit_events`.

### 13. Moving the running system to MT

**Decision:** convert everything **at once**, with no transition period and no backward compatibility. There are no real users, and the AI coding agent carries out the conversion; it needs these instructions:

| Today | After |
| --- | --- |
| Existing data (services, routes, consumers, keys) | Stays in the `default` workspace (every row already has `workspace_id`) |
| The current admin (argon2 password in `admins`) | Re-created in GoTrue at startup (passwords can't move: different hashing) |
| `admins` table | An Alembic migration turns it into `users` + `workspace_members` (Owner of `default`, `is_platform_admin = true`); the argon2 hash is dropped |
| `/api/v1/services` and the other old URLs | Removed; tests, e2e and `openapi.json` move to `/api/v1/workspaces/{slug}/...` in the same milestone |
| Global `config_version` and the old snapshot | Replaced by `config_seq`, per-workspace versions and the new snapshot (decision 7); gateway and control plane change together |
| `services.upstream_url` | Becomes one row in `service_targets` (decision 14) |

All 333 control-plane tests, the gateway tests and the 15 e2e stories must pass at the end, updated to the new shapes; the coverage gates stay at 90%.

### 14. Load balancing and health checks

**Decision:** a service gets several **targets**; the gateway spreads requests with **weighted round robin** and avoids unhealthy targets using active and passive health checks.

```
service_targets (service_id, url, weight, is_active)
```

- `services.upstream_url` becomes a list of targets; existing services get one target each (decision 13).
- **Weighted round robin:** targets in turn, in proportion to their weights (e.g. 100 / 100 / 50). Simple and predictable; the default in Kong and Envoy.
- **Active health checks:** each gateway calls each target's `/health` every 10 s; 3 failures in a row → unhealthy; 2 successes → healthy again.
- **Passive health checks:** from real traffic, 5 connection errors or 5xx in a row → the target is skipped for 30 s.
- Each gateway instance checks on its own, without sharing state; normal and acceptable.
- All targets unhealthy: `503 {"error":"no_healthy_upstream"}`.
- The SSRF check (decision 3) applies to every target.
- Target health and weights go into logs (`target` field) and, in v2, metrics.

**Deferred:** least request (power of two choices) as a per-service option; consistent hashing; retries on another target.

### 15. CORS per route (details proposed)

**Decision:** browser apps can call APIs through the gateway; each route can enable CORS.

- Route settings: `cors_origins` (list, or empty = CORS off), `cors_methods`, `cors_headers`, `cors_credentials` (bool), `cors_max_age`. Off by default.
- The gateway answers the browser's **preflight** (`OPTIONS` with `Access-Control-Request-Method`) itself, right after route matching by path and **before auth** (a preflight never carries an API key), and doesn't forward it.
- On normal requests the gateway adds `Access-Control-Allow-Origin` (the matching origin, never `*` together with credentials) and `Vary: Origin`.
- Preflights don't spend rate-limit tokens.

### 16. Request body size limit (details proposed)

**Decision:** the gateway refuses bodies that are too large before they reach a backend.

- Platform default `MAX_REQUEST_BODY=10MB`; a route can set its own limit up to a platform maximum (e.g. 100 MB).
- A `Content-Length` over the limit → `413 {"error":"request_too_large"}` at once, without reading the body. Bodies without a length (chunked) are wrapped with `http.MaxBytesReader` and cut at the limit.
- The limit appears in the log line when hit.

### 17. `workspace_id` in logs and metrics

**Decision:** every gateway log line and every control-plane log line carries `workspace_id`; v2 Prometheus metrics get a `workspace` label (bounded by the number of workspaces). This lets each workspace's traffic and errors be filtered, and later shown to its members.

---


## ⬜ Features not placed in the roadmap yet

From comparing with Kong and Apigee; to be placed in phases when writing the plan:

| Feature | Importance |
| --- | --- |
| OpenAPI import (routes and portal docs from `openapi.yaml`) | Important |
| IP allow / deny lists per route or consumer | Important |
| Upstream credentials (the gateway adds a secret for the backend) | Important |
| Config as code: export / import all settings as YAML (like Kong's decK) | Important |
| Alerts (quota at 80%, many 5xx, new key created) | Important |
| Production deployment guide (Caddy, TLS, DNS, Postgres backups, zero-downtime updates) | Important |
| WebSockets, gRPC, GraphQL | Later |
| Monetization and billing | Out of scope |

---

## ⏳ Deferred (decided "later", with the design leaving room)

| Item | When | Note |
| --- | --- | --- |
| Postgres Row-Level Security | Later | Every tenant table already has `workspace_id` |
| Custom roles and narrower scopes (e.g. API Admin on one service only) | Later | Move the role → permission map into tables; add `scope` to memberships |
| SSO (OIDC / SAML) for tenants | Later | GoTrue supports it; roles stay ours |
| Dedicated gateway per large workspace (hybrid model) | Later | Same gateway with `GATEWAY_WORKSPACES=acme` |
| Custom domains, TLS, Caddy | Deployment | `workspace_hosts` table |
| Asymmetric JWT signing (ES256 / EdDSA) | v3 | Needed if the gateway verifies portal tokens |
| Separate GoTrue instance for portal developers | v3 | Open sign-up there, closed for the dashboard |
| Invitation accept / decline flow (consent, expiry, cancel) | Later | Decision 8 chose the simple option; the add-member endpoint can switch to creating invitations |
| Radix tree for route matching | Only if benchmarks ask | Today 4–5 µs for 1000 routes |
| Fair queuing, priority by plan | v2 or later | |
| Export a workspace's configuration before deletion | Later | JSON download |
| CAPTCHA on login | If attacks appear | GoTrue supports it |
| Service accounts (long-lived tokens for CI, Terraform) | Later | Non-human members with a role; `require_permission` works the same; never Owner |
| Least request (P2C), consistent hashing, retries | Later | Decision 14 starts with weighted round robin |
| Owner can require MFA for the whole workspace | Later | Check `aal2` per workspace setting |
