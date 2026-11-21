"""Writing (and, from the endpoints, reading) audit events."""

import ipaddress
import uuid
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.changes import Changes
from app.audit.context import AuditContext
from app.audit.models import AuditEvent


def record(
    db: AsyncSession,
    audit: AuditContext,
    *,
    workspace_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    target_name: str | None,
    changes: Changes,
) -> None:
    """Stage an audit event in the caller's transaction (decision 11).

    It commits or rolls back together with the change it describes, so a
    failed change leaves no event and a saved one always has its event.
    """
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_type=audit.actor_type,
            actor_user_id=audit.actor_user_id,
            actor_email=audit.actor_email,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            changes=changes,
            request_id=audit.request_id,
            ip=_parse_ip(audit.ip),
            user_agent=audit.user_agent,
        )
    )


def _parse_ip(address: str | None) -> IPv4Address | IPv6Address | None:
    """The client's address, if it is an IP (e.g. not a Unix socket's name)."""
    try:
        return ipaddress.ip_address(address) if address else None
    except ValueError:
        return None
