"""Request and response shapes for /api/v1/me and /api/v1/users."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.permissions import Role
from app.core.schemas import PartialUpdate
from app.workspaces.models import WorkspaceStatus


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    is_platform_admin: bool
    is_active: bool
    created_at: datetime


class Membership(BaseModel):
    """One workspace a person belongs to, with their role there."""

    slug: str
    name: str
    role: Role
    status: WorkspaceStatus


class UserListItem(UserOut):
    """GET /api/v1/users: a person and their workspaces (deleted ones included)."""

    workspaces: list[Membership]


class UserUpdate(PartialUpdate):
    """PATCH /api/v1/users/{id}: disable or enable a person."""

    is_active: bool | None = None
