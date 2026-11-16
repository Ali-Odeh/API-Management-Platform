"""The app's startup (lifespan) runs the real seeding, as in production."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import crud as auth_crud
from app.auth.models import Admin
from app.core.config import get_settings
from app.main import app
from app.workspaces.models import Workspace


async def test_startup_seeds_default_workspace_and_admin(db: AsyncSession) -> None:
    settings = get_settings()

    # Run startup + shutdown exactly as uvicorn would.
    async with app.router.lifespan_context(app):
        pass

    admin = await auth_crud.get_admin_by_email(db, settings.admin_email)
    assert admin is not None
    workspace = await db.get(Workspace, admin.workspace_id)
    assert workspace is not None and workspace.slug == "default"
    # The seeded admin can log in with the password from the environment.
    assert await auth_crud.authenticate(db, settings.admin_email, settings.admin_password)


async def test_restarting_does_not_duplicate_anything(db: AsyncSession) -> None:
    for _ in range(2):
        async with app.router.lifespan_context(app):
            pass

    assert await db.scalar(select(func.count()).select_from(Admin)) == 1
    assert await db.scalar(select(func.count()).select_from(Workspace)) == 1
