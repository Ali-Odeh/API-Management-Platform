"""Writing and reading audit events."""

import ipaddress
import uuid
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.changes import Changes
from app.audit.context import AuditContext
from app.audit.models import AuditEvent
from app.audit.schemas import AuditFilters
from app.core.pagination import PageParams, paginate


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


async def list_workspace_events(
    db: AsyncSession, workspace_id: uuid.UUID, *, filters: AuditFilters, page: PageParams
) -> tuple[list[AuditEvent], int]:
    """A workspace's log, newest first: its members' and the Platform Admin's changes there."""
    query = _filtered(filters).where(AuditEvent.workspace_id == workspace_id)
    return await paginate(db, query, page)


async def list_all_events(
    db: AsyncSession,
    *,
    filters: AuditFilters,
    page: PageParams,
    workspace_id: uuid.UUID | None = None,
) -> tuple[list[AuditEvent], int]:
    """Every event, platform-level ones included, newest first (Platform Admin)."""
    query = _filtered(filters)
    if workspace_id is not None:
        query = query.where(AuditEvent.workspace_id == workspace_id)
    return await paginate(db, query, page)


def _filtered(filters: AuditFilters) -> Select[tuple[AuditEvent]]:
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id)
    if filters.actor is not None:
        query = query.where(AuditEvent.actor_email == filters.actor.strip().lower())
    if filters.action is not None:
        query = query.where(AuditEvent.action == filters.action)
    if filters.target_type is not None:
        query = query.where(AuditEvent.target_type == filters.target_type)
    if filters.since is not None:
        query = query.where(AuditEvent.created_at >= filters.since)
    if filters.until is not None:
        query = query.where(AuditEvent.created_at < filters.until)
    return query
