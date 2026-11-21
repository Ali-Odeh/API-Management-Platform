"""Creates the FastAPI app. Each feature's router gets included here."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.audit import crud as audit
from app.audit.context import AuditContext
from app.audit.router import router as audit_router
from app.consumers.router import router as consumers_router
from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, driver_dsn, engine
from app.core.errors import register_error_handlers
from app.core.openapi import error_responses
from app.core.permissions import Role
from app.core.request_id import RequestIdMiddleware
from app.gateway_config.notifier import ConfigNotifier
from app.gateway_config.provider import ConfigProvider
from app.gateway_config.router import router as gateway_config_router
from app.gotrue import client as gotrue_client
from app.gotrue.client import GoTrueAdmin
from app.keys.router import router as keys_router
from app.members import crud as members_crud
from app.routes.router import router as routes_router
from app.services.router import router as services_router
from app.users import crud as users_crud
from app.users.router import router as users_router
from app.workspaces.crud import get_or_create_default_workspace
from app.workspaces.router import router as workspaces_router


async def _bootstrap(gotrue: GoTrueAdmin, settings: Settings) -> None:
    """Make sure the `default` workspace, the first Platform Admin and an Owner exist.

    Safe on every startup: nothing changes the second time. (Tables already
    exist: the container runs `alembic upgrade head` first.) A fresh install
    gets the Platform Admin as Owner of `default`, as the migration does for
    an upgraded one; a workspace always has an Owner.
    """
    async with SessionLocal() as db:
        workspace = await get_or_create_default_workspace(db)
        # Commits the workspace together with the Platform Admin's row.
        admin = await users_crud.ensure_platform_admin(
            db,
            gotrue,
            email=settings.platform_admin_email,
            password=settings.platform_admin_password,
        )
        if admin.is_platform_admin and not await members_crud.has_owner(db, workspace.id):
            member = members_crud.add_member(
                db, workspace.id, user_id=admin.id, role=Role.OWNER, added_by=None
            )
            audit.record(
                db,
                AuditContext.system(),
                workspace_id=workspace.id,
                action="member.add",
                target_type="member",
                target_id=member.id,
                target_name=admin.email,
                changes={"role": [None, Role.OWNER.value]},
            )
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Looked up through the module, so tests can swap in a fake GoTrue.
    gotrue = gotrue_client.create_gotrue_admin(settings)
    app.state.gotrue = gotrue
    await _bootstrap(gotrue, settings)

    # Start listening for config changes, for long-polling gateways.
    notifier = ConfigNotifier(driver_dsn(settings.database_url))
    await notifier.start()
    app.state.config_provider = ConfigProvider(engine, notifier)
    yield
    # Shutdown: stop listening (waiting requests answer at once), close pooled connections.
    await notifier.stop()
    await gotrue.aclose()
    await engine.dispose()


app = FastAPI(
    title="API Management Platform - Control Plane",
    version="0.1.0",
    lifespan=lifespan,
)

register_error_handlers(app)
app.add_middleware(RequestIdMiddleware)

# Every signed-in endpoint can answer these; document them once for all in Swagger.
_SIGNED_IN = error_responses(
    {
        401: "Missing, invalid or expired GoTrue access token",
        403: "Not allowed: user_disabled, account_conflict, platform_admin_required, "
        "mfa_required or permission_denied",
    }
)

# Every endpoint under /workspaces/{slug} answers 404 for an unknown workspace
# or one the caller isn't a member of (it doesn't reveal that it exists).
_IN_WORKSPACE = _SIGNED_IN | error_responses({404: "Workspace not found"})

app.include_router(users_router, responses=_SIGNED_IN)
app.include_router(workspaces_router, responses=_SIGNED_IN)
app.include_router(services_router, responses=_IN_WORKSPACE)
app.include_router(routes_router, responses=_IN_WORKSPACE)
app.include_router(consumers_router, responses=_IN_WORKSPACE)
app.include_router(keys_router, responses=_IN_WORKSPACE)
app.include_router(audit_router, responses=_IN_WORKSPACE)
app.include_router(gateway_config_router)  # documents its own 401 (internal token)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness: the process is up. Deliberately does not touch the DB."""
    return {"status": "ok"}
