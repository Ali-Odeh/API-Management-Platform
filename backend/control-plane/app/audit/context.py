"""Who is acting and from where: what every audit event records about the request."""

import uuid
from dataclasses import dataclass

from app.audit.models import ActorType

_USER_AGENT_MAX = 512


@dataclass(frozen=True)
class AuditContext:
    actor_type: ActorType
    actor_user_id: uuid.UUID | None = None
    actor_email: str | None = None
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None

    @classmethod
    def system(cls) -> "AuditContext":
        """Startup, scripts and jobs: no person, no request."""
        return cls(actor_type=ActorType.SYSTEM)

    @staticmethod
    def clip_user_agent(user_agent: str | None) -> str | None:
        return user_agent[:_USER_AGENT_MAX] if user_agent else None
