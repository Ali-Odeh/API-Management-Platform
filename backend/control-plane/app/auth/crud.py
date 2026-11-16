"""All database work for admins. Endpoints never write queries themselves."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Admin
from app.core.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.workspaces.crud import get_or_create_default_workspace


def normalize_email(email: str) -> str:
    """'  Admin@Example.COM ' and 'admin@example.com' are the same admin."""
    return email.strip().lower()


async def get_admin_by_email(db: AsyncSession, email: str) -> Admin | None:
    return await db.scalar(select(Admin).where(Admin.email == normalize_email(email)))


async def get_admin_by_id(db: AsyncSession, admin_id: uuid.UUID) -> Admin | None:
    return await db.get(Admin, admin_id)


async def create_admin(
    db: AsyncSession, *, email: str, password: str, workspace_id: uuid.UUID
) -> Admin:
    admin = Admin(
        email=normalize_email(email),
        password_hash=hash_password(password),
        workspace_id=workspace_id,
    )
    db.add(admin)
    await db.flush()
    return admin


async def authenticate(db: AsyncSession, email: str, password: str) -> Admin | None:
    """Return the admin if email and password match, else None."""
    admin = await get_admin_by_email(db, email)
    if admin is None:
        # Still do the slow hash check, so "unknown email" and "wrong password"
        # take the same time (no timing leak).
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, admin.password_hash):
        return None
    return admin


async def seed_admin(db: AsyncSession, *, email: str, password: str) -> Admin:
    """Make sure the default workspace and the first admin exist.

    Safe to run on every startup: if the admin already exists, nothing
    changes (its password is NOT overwritten).
    """
    workspace = await get_or_create_default_workspace(db)
    admin = await get_admin_by_email(db, email)
    if admin is None:
        admin = await create_admin(
            db, email=email, password=password, workspace_id=workspace.id
        )
    await db.commit()
    return admin
