"""Request and response shapes for /api/v1/workspaces/{slug}/members."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.core.permissions import Role
from app.core.schemas import PartialUpdate, PersonEmail


class MemberCreate(BaseModel):
    """Add a person by email: someone new gets an invitation email (decision 8)."""

    email: PersonEmail
    role: Role


class MemberUpdate(PartialUpdate):
    role: Role | None = None


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: Role
    is_active: bool  # a disabled person can't act, and never counts as an Owner
    added_by: uuid.UUID | None
    created_at: datetime


class MemberAdded(MemberOut):
    invited: bool  # True: a new person, sent an invitation email
