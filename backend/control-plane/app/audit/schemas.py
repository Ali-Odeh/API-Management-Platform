"""Response shape and filters for the audit endpoints."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, IPvAnyAddress

from app.audit.models import ActorType


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    workspace_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    actor_type: ActorType
    action: str
    target_type: str
    target_id: uuid.UUID | None
    target_name: str | None
    changes: dict[str, Any]
    request_id: str | None
    ip: IPvAnyAddress | None
    user_agent: str | None


@dataclass(frozen=True)
class AuditFilters:
    actor: str | None = None
    action: str | None = None
    target_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def _audit_filters(
    actor: Annotated[str | None, Query(description="Who acted (email, any case)")] = None,
    action: Annotated[str | None, Query(examples=["key.revoke"])] = None,
    target_type: Annotated[str | None, Query(examples=["service"])] = None,
    since: Annotated[
        datetime | None, Query(alias="from", description="At or after this time")
    ] = None,
    until: Annotated[datetime | None, Query(alias="to", description="Before this time")] = None,
) -> AuditFilters:
    return AuditFilters(
        actor=actor, action=action, target_type=target_type, since=since, until=until
    )


Filters = Annotated[AuditFilters, Depends(_audit_filters)]
