"""API tests for /api/v1/auth, plus seeding."""

import uuid

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import crud as auth_crud
from app.auth.models import Admin
from app.core.security import create_access_token
from app.workspaces.models import Workspace
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


# ---- Login


async def test_login_success_returns_token(client: AsyncClient, admin: Admin) -> None:
    r = await client.post(LOGIN, json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 60 * 60
    assert body["access_token"].count(".") == 2  # a JWT has 3 parts


async def test_login_email_is_case_insensitive(client: AsyncClient, admin: Admin) -> None:
    r = await client.post(LOGIN, json={"email": "  ADMIN@Test.Local ", "password": ADMIN_PASSWORD})
    assert r.status_code == 200


async def test_login_wrong_password(client: AsyncClient, admin: Admin) -> None:
    r = await client.post(LOGIN, json={"email": ADMIN_EMAIL, "password": "wrong"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid email or password"}


async def test_login_unknown_email_gives_same_error(client: AsyncClient, admin: Admin) -> None:
    r = await client.post(LOGIN, json={"email": "nobody@test.local", "password": "x"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid email or password"}


async def test_login_missing_fields_is_422(client: AsyncClient) -> None:
    r = await client.post(LOGIN, json={"email": ADMIN_EMAIL})
    assert r.status_code == 422


# ---- /me


async def test_me_with_token_from_login(client: AsyncClient, admin: Admin) -> None:
    login = await client.post(LOGIN, json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    token = login.json()["access_token"]

    r = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(admin.id)
    assert body["email"] == ADMIN_EMAIL
    assert body["workspace_id"] == str(admin.workspace_id)
    assert "password_hash" not in body


async def test_me_without_token_is_401(client: AsyncClient) -> None:
    r = await client.get(ME)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"


async def test_me_with_garbage_token_is_401(client: AsyncClient) -> None:
    r = await client.get(ME, headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


async def test_me_with_expired_token_is_401(client: AsyncClient, admin: Admin) -> None:
    token = create_access_token(str(admin.id), expires_minutes=-1)
    r = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_me_for_deleted_admin_is_401(client: AsyncClient) -> None:
    token = create_access_token(str(uuid.uuid4()))  # valid token, no such admin
    r = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_me_with_non_uuid_subject_is_401(client: AsyncClient) -> None:
    token = create_access_token("not-a-uuid")
    r = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---- Seeding


async def test_seed_creates_default_workspace_and_admin(db: AsyncSession) -> None:
    admin = await auth_crud.seed_admin(db, email="Seed@Test.Local", password="pw-1")

    workspace = await db.get(Workspace, admin.workspace_id)
    assert workspace is not None and workspace.slug == "default"
    assert admin.email == "seed@test.local"


async def test_seed_is_idempotent_and_keeps_password(db: AsyncSession) -> None:
    first = await auth_crud.seed_admin(db, email="seed@test.local", password="pw-1")
    second = await auth_crud.seed_admin(db, email="seed@test.local", password="pw-2")

    assert first.id == second.id
    assert await db.scalar(select(func.count()).select_from(Admin)) == 1
    assert await db.scalar(select(func.count()).select_from(Workspace)) == 1
    # Restarting with a different ADMIN_PASSWORD must not change the password.
    assert await auth_crud.authenticate(db, "seed@test.local", "pw-1") is not None
    assert await auth_crud.authenticate(db, "seed@test.local", "pw-2") is None
