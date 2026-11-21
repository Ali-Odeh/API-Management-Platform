"""Small helpers that create test data in one line, with sensible defaults.

    service = await make_service(db, workspace.id)
    route = await make_route(db, workspace.id, service.id, path_prefix="/v1/users")

Each helper commits, so database rules (unique, check, foreign key) are
enforced right there; use `expect_violation` to assert that one fails.
"""

import uuid
from collections.abc import Awaitable
from typing import Any, TypeVar

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.consumers.models import Consumer
from app.core.db import violated_constraint
from app.core.permissions import Role
from app.core.security import generate_api_key, hash_api_key
from app.keys.models import KEY_PREFIX_LENGTH, ApiKey
from app.members.models import WorkspaceMember
from app.routes.models import Route
from app.services.models import Service
from app.users.models import User
from app.workspaces.models import Workspace

T = TypeVar("T")


async def _save(db: AsyncSession, obj: T) -> T:
    db.add(obj)
    await db.commit()
    return obj


async def make_workspace(db: AsyncSession, slug: str = "other", **fields: Any) -> Workspace:
    return await _save(db, Workspace(name=slug.title(), slug=slug, **fields))


async def make_user(
    db: AsyncSession, email: str = "someone@test.local", *, linked: bool = True, **fields: Any
) -> User:
    """A person; `linked` gives them a GoTrue account id, as after a first login."""
    if linked:
        fields.setdefault("auth_user_id", uuid.uuid4())
    return await _save(db, User(email=email, **fields))


async def make_member(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, role: Role
) -> WorkspaceMember:
    return await _save(db, WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role))


async def make_service(
    db: AsyncSession, workspace_id: uuid.UUID, name: str = "orders", **fields: Any
) -> Service:
    fields.setdefault("upstream_url", "http://mock-backend:9000")
    return await _save(db, Service(workspace_id=workspace_id, name=name, **fields))


async def make_route(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    service_id: uuid.UUID,
    path_prefix: str = "/v1/orders",
    **fields: Any,
) -> Route:
    fields.setdefault("methods", ["GET", "POST"])
    return await _save(
        db,
        Route(workspace_id=workspace_id, service_id=service_id, path_prefix=path_prefix, **fields),
    )


async def make_consumer(
    db: AsyncSession, workspace_id: uuid.UUID, name: str = "mobile-app", **fields: Any
) -> Consumer:
    fields.setdefault("email", "dev@example.com")
    return await _save(db, Consumer(workspace_id=workspace_id, name=name, **fields))


async def make_api_key(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    consumer_id: uuid.UUID,
    raw_key: str | None = None,
    **fields: Any,
) -> ApiKey:
    raw = raw_key or generate_api_key()
    fields.setdefault("name", "test key")
    fields.setdefault("key_prefix", raw[:KEY_PREFIX_LENGTH])
    fields.setdefault("key_hash", hash_api_key(raw))
    return await _save(db, ApiKey(workspace_id=workspace_id, consumer_id=consumer_id, **fields))


async def expect_violation(db: AsyncSession, constraint: str, action: Awaitable[Any]) -> None:
    """Assert that `action` is rejected by exactly the named database constraint."""
    with pytest.raises(IntegrityError) as caught:
        await action
    await db.rollback()
    assert violated_constraint(caught.value) == constraint
