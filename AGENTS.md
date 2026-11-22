# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, or any other) working in this repository. `CLAUDE.md` points here, so every agent follows the same rules.

## The project

A self-hosted API management platform, built as a learning and portfolio project: a **Go gateway** every API request passes through, a **FastAPI control plane** that configures it, **GoTrue** (Supabase Auth) for login, Postgres, Redis, and later a React dashboard. Everything runs with Docker Compose.

| Document | Holds |
| --- | --- |
| `docs/PLAN.md` | **What** is built and **how**: architecture, data model, API, gateway design, settings. The source of truth for behavior |
| `docs/ROADMAP.md` | **Progress**: milestones, steps with "done when" criteria, quality numbers, the decisions log |
| `docs/MT_ROLES_DECISIONS.md` | **Why**: the multi-tenancy and login decisions, with the options not chosen |
| `docs/DEVELOPMENT.md` | **Commands**: running, testing and checking everything |

Read the relevant parts of all four before starting a step.

## How a step is worked

The owner of this project works one roadmap step at a time (for example `MT.4`) and expects the agent to **finish the whole step on its own**.

1. **Read** the step in `ROADMAP.md`, the PLAN.md sections it touches, and the decisions it references.
2. **Plan** your own sub-tasks. How you split the work is up to you.
3. **Build the whole step without stopping between sub-tasks.** Do not hand back after each file, endpoint or test. Do not ask for confirmation of things the documents already decide.
4. **Test as you go**, then run every check (below) and fix what fails.
5. **Commit** in logical commits along the way (below).
6. **Update the documents** (below).
7. **Hand back once**, when every "done when" item of the step holds, with a short report:
   - what was built, as a list of the commits
   - test and check results (counts, coverage, e2e)
   - any deviation from PLAN.md and why (also in the decisions log)
   - anything left open or worth a look

**Stop early only when:**

- the documents contradict each other or the code in a way you can't resolve from them, and the choice changes behavior a user or client would see;
- the step needs something you can't get (a secret, a tool, network access);
- an action can't be undone and isn't part of the step (deleting real data, rewriting pushed history, changing CI secrets).

In those cases stop, explain the problem and the options, and recommend one.

## Decisions while building

- **Implementation choices are yours:** names, internal structure, helper functions, libraries already in use, test layout. Take the better option; no need to ask.
- **Contracts are decided:** API paths and shapes, status codes and error bodies, the data model, security rules and settings in PLAN.md. Keep them. If one turns out to be wrong or impossible, change it **openly**: update PLAN.md, add a row to the decisions log in ROADMAP.md (date, step, decision, why), and mention it in the hand-back report. Never change decided behavior silently.
- New dependencies: prefer the standard library (Go) and what the project already uses. Add a dependency only when it clearly earns its place, and record it in the decisions log.

## Commits

- **Logical commits:** each commit is one coherent change that can be understood and reverted on its own (a migration with its model, an endpoint with its tests, a gateway middleware with its tests, a docs update). Not one giant commit per step, and not "wip" or "fix typo" noise.
- Every commit builds, and its tests pass.
- Message: a short imperative subject (≤ 72 characters), a blank line, then **why** in a few lines when it isn't obvious. Mention the step, e.g. `MT.4: add last-owner rule with row lock`.
- No AI attribution: no `Co-Authored-By` trailer for an agent in commits, no "generated with" line in pull requests.
- Never commit secrets, `.env`, generated local data, or large binaries.
- Don't rewrite history that has been pushed.

## Code quality

The code must be **clean, readable, maintainable and scalable**. Concretely:

**Structure**

- Control plane: one folder per feature (`router.py`, `models.py`, `schemas.py`, `crud.py`). Routers stay thin: HTTP in, call `crud`/logic, HTTP out. Business rules live in `crud.py` or a feature's own module, never in routers. Shared pieces go in `core/`.
- Gateway: one package per responsibility under `internal/`. `main.go` only wires things together. Packages don't import each other in circles; per-request facts travel in `reqctx`.
- Follow the existing patterns of the codebase before inventing new ones (error helpers, pagination, `respond`, settings).
- Routers never write queries. `crud.py` never raises `HTTPException`: it raises the domain errors in `app/core/errors.py` (`NotFoundError`, `ConflictError`, ...), which map to `{"detail": ...}` with the right status.
- Reuse the shared core instead of copying it: the mixins in `core/models.py` (uuid id, timestamps, workspace), the dependencies in `core/deps.py`, `Pagination` / `Page[T]` in `core/pagination.py`, the errors in `core/errors.py`.
- New models are imported in `alembic/env.py`. Migrations are autogenerated in the container, then reviewed and edited by hand before they are applied.
- Go: small packages with one responsibility, interfaces at boundaries.

**Readability**

- Clear names over comments; comments explain **why**, not what.
- Small functions with one job. No dead code, no commented-out code, no TODOs without a roadmap step.
- Python: full type hints (mypy strict), Pydantic models at the edges, async all the way to the database.
- Go: `gofmt`, `go vet`, errors wrapped with context, `context.Context` passed through, no goroutine leaks (everything started is stopped), table-driven tests.

**Correctness and safety (non-negotiable)**

- Every query on tenant data takes `workspace_id` and filters by it.
- Every admin endpoint declares a permission with `require_permission(...)`.
- Every write records its audit event in the same transaction.
- Secrets never appear in logs, errors, audit events or API answers (API keys only by prefix).
- Input is validated at the edge; the database enforces what it can (constraints, foreign keys, CHECKs).
- Concurrency rules are enforced with locks or atomic operations, and tested in parallel.

**Performance (gateway hot path)**

- No database or control-plane calls per request; one Redis round trip at most.
- Avoid needless allocations; when changing matching, auth, the balancer or the chain, run the benchmarks and record numbers in ROADMAP.md if they moved.

## Tests

- Tests are written **with** the code, in the same commit or the one right after.
- Coverage stays **≥ 90%** for both services (enforced by the check scripts and CI).
- The guard tests (permissions, isolation, audit) must cover every endpoint; a new endpoint that breaks them is not done.
- Parsers and security checks (paths, hosts, IP policy, request IDs) get fuzz tests.
- Behavior a client can see gets an e2e story in `e2e/`.
- Tests clean up after themselves and never touch real data (`apim_test`, Redis database 15).
- Control-plane tests use the `apim_test` database, which `tests/conftest.py` recreates and migrates on every run; shared fixtures live there.
- Gateway Redis tests share database 15 and run in parallel packages: they never `FLUSHDB`, and each test uses its own consumer and workspace IDs.
- e2e tests create their own objects and always delete them, so they never touch existing data.

## Checks before handing back

Commands are in `docs/DEVELOPMENT.md` (PowerShell, from the repo root). All must be green:

```powershell
.\backend\control-plane\scripts\check.ps1     # ruff, mypy strict, pytest, coverage >= 90%
.\backend\gateway\scripts\check.ps1           # gofmt, go vet, go test, coverage >= 90%
docker compose up -d --build --wait
docker compose run --rm e2e                   # end-to-end stories
```

Plus, for gateway changes, the race detector in the Linux Go image (see DEVELOPMENT.md). If the API changed, regenerate and commit `backend/control-plane/openapi.json` (`check.ps1 -Fix` or `scripts/export_openapi.py`): it is the committed API contract.

The check scripts must also pass before **every commit**, not only before handing back. `.github/workflows/ci.yml` runs the same checks on every push and pull request (plus `-race`, a 10 s run of every fuzz test, an image build and the e2e tests on a fresh stack); keep it in step with the `check.ps1` scripts.

## Documents to update at the end of a step

- `docs/ROADMAP.md`: the step's status and commits, the overview table, the "Quality today" numbers, and the decisions log for any choice that differs from or adds to the plan.
- `docs/PLAN.md`: only if a contract changed (see "Decisions while building").
- `docs/DEVELOPMENT.md` and `README.md`: when commands, services, ports or settings changed.
- `.env.example` and `scripts/init-env.*`: when settings changed.

## Learning

The owner is a backend engineer (FastAPI, Spring Boot, Node) and **new to Go**; this project is partly for learning. In the hand-back report, briefly explain each Go concept, library or pattern that appeared for the first time in the step (a few lines each, with the file where it is used).

## Git

- Remote `origin` is the owner's GitHub repository. **Never push**: the owner pushes.
- Work on the current branch unless the owner asks for another.

## Environment

- The owner works on **Windows with PowerShell**; give commands in PowerShell form. Python is run with `py` (plain `python` is the Windows Store alias).
- Apps run **only in Docker** (one set of addresses). Host ports: Postgres `5434`, gateway `8081`, control plane `8000`, Mailpit UI `8025`.
  - The control plane always uses `DATABASE_URL=...@postgres:5432/...`. Run pytest and Alembic in the database setup container (`docker compose run --rm control-plane-db-init pytest`, `docker compose run --rm control-plane-db-init alembic ...`). The code is bind-mounted and uvicorn runs with `--reload`. The local `.venv` only serves editor autocomplete.
  - Go tests run locally with `go test ./...` (the gateway has no database); the gateway itself runs in Docker, rebuilt after a change with `docker compose up -d --build gateway` (Go has no reload).
  - The owner has local Windows Postgres on 5432 and 5433, so Docker's Postgres is on host port 5434 (only for tools like pgAdmin). Another Windows app holds 127.0.0.1:8080, so the gateway is on host port 8081; containers still use `gateway:8080`.
- Line endings: LF everywhere, CRLF for `.ps1` (`.gitattributes`).

## Reminders for later milestones

Raise these with the owner when the milestone starts (details in PLAN.md section 12, "Patterns to apply later"):

- **V2.1 usage events:** they go over a Redis Stream (decided Nov 22, 2026, replacing the inbox pattern): the gateway appends them off the request's path (buffered, pipelined `XADD`), so a request still makes at most one Redis round trip; the `usage-ingest` worker acks only after its commit, and `event_id` makes a re-read entry insert once.
- **V2.1 `last_used_at`:** the gateway never writes to Postgres; the worker fills the column from the usage events, and it is not config (it must not bump the config sequence).
- **Webhooks** (*out of plan*): if they ever come, use the **outbox** pattern (outbox row in the same transaction, a worker delivers with retries).
