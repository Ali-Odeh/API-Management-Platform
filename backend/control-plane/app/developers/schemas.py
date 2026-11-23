"""Request and response shapes for developers (the Platform Admin's list and
block, and the portal's /me)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.schemas import PartialUpdate
from app.developers.models import DeveloperStatus


class DeveloperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    status: DeveloperStatus
    created_at: datetime


class DeveloperUpdate(PartialUpdate):
    """PATCH /api/v1/developers/{id}: `{"status": "blocked"}` closes the portal to them."""

    status: DeveloperStatus | None = None
