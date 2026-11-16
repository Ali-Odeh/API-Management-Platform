"""Request and response shapes for /api/v1/auth (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255, examples=["admin@example.com"])
    password: str = Field(min_length=1, max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")


class AdminOut(BaseModel):
    # from_attributes: build this from a SQLAlchemy object (admin.email, ...).
    # password_hash is simply not listed, so it can never leak in a response.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    workspace_id: uuid.UUID
    created_at: datetime
