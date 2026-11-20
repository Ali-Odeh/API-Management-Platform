"""Request and response shapes for /api/v1/me and /api/v1/users."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.schemas import PartialUpdate


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    is_platform_admin: bool
    is_active: bool
    created_at: datetime


class UserUpdate(PartialUpdate):
    """PATCH /api/v1/users/{id}: disable or enable a person."""

    is_active: bool | None = None
