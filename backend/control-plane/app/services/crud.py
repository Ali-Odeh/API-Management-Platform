"""All database work for services and their targets.

Every function takes `workspace_id` and filters by it (option C), so an
admin can only ever see and change services of their own workspace. A
service in another workspace behaves exactly like one that doesn't exist.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes, created, deleted, snapshot, updated
from app.audit.context import AuditContext
from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.pagination import PageParams, paginate
from app.routes.models import FK_ROUTE_SERVICE
from app.services.models import UQ_SERVICE_NAME, UQ_TARGET_URL, Service, ServiceTarget
from app.services.schemas import (
    MAX_TARGETS_PER_SERVICE,
    ServiceCreate,
    ServiceUpdate,
    TargetCreate,
    TargetUpdate,
)
from app.workspaces.limits import check_quota

# What a service's audit events record (its whole configuration; its
# targets on creation, and in their own events afterwards).
AUDIT_FIELDS = (
    "name",
    "timeout_ms",
    "is_active",
    "lb_algorithm",
    "hc_path",
    "hc_interval_s",
    "hc_unhealthy_threshold",
    "hc_healthy_threshold",
    "passive_failures",
    "passive_eject_s",
)
TARGET_AUDIT_FIELDS = ("service_id", "url", "weight", "is_active")


def _name_taken(name: str) -> dict[str, AppError]:
    return {UQ_SERVICE_NAME: ConflictError(f"A service named '{name}' already exists")}


_URL_TAKEN: dict[str, AppError] = {
    UQ_TARGET_URL: ConflictError("This service already has a target with that URL")
}


# ---- Services


async def list_services(
    db: AsyncSession, workspace_id: uuid.UUID, *, page: PageParams
) -> tuple[list[Service], int]:
    """One page of services (newest first, each with its targets) and the total count."""
    query = (
        select(Service)
        .where(Service.workspace_id == workspace_id)
        .order_by(Service.created_at.desc(), Service.id)
    )
    return await paginate(db, query, page)


async def get_service(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    service_id: uuid.UUID,
    for_update: bool = False,
) -> Service:
    """`for_update=True` locks the service's row until commit, so parallel
    changes to its targets (adding up to the limit, removing the last one)
    run one after the other."""
    query = (
        select(Service)
        .where(Service.id == service_id, Service.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    service = await db.scalar(query.with_for_update() if for_update else query)
    if service is None:
        raise NotFoundError("Service not found")
    return service


async def create_service(
    db: AsyncSession, workspace_id: uuid.UUID, *, data: ServiceCreate, audit: AuditContext
) -> Service:
    await check_quota(db, workspace_id, "max_services")
    # The ids are set here so the audit event can name them before the commit.
    service = Service(
        id=uuid.uuid4(), workspace_id=workspace_id, **data.model_dump(exclude={"targets"})
    )
    # Targets are listed oldest first. Postgres's now() is the same for the
    # whole transaction, so each one gets its own moment, in the order sent.
    now = datetime.now(UTC)
    service.targets = [
        ServiceTarget(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            created_at=now + timedelta(microseconds=i),
            **t.model_dump(),
        )
        for i, t in enumerate(data.targets)
    ]
    db.add(service)
    after: dict[str, Any] = snapshot(service, AUDIT_FIELDS)
    after["targets"] = [snapshot(t, ("url", "weight", "is_active")) for t in service.targets]
    _record(db, audit, service, "service.create", created(after))
    await commit_or_raise(db, _name_taken(data.name))
    return await get_service(db, workspace_id=workspace_id, service_id=service.id)


async def update_service(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    service_id: uuid.UUID,
    data: ServiceUpdate,
    audit: AuditContext,
) -> Service:
    service = await get_service(db, workspace_id=workspace_id, service_id=service_id)
    before = snapshot(service, AUDIT_FIELDS)
    sent = data.model_dump(exclude_unset=True)  # only the fields that were sent
    for field, value in sent.items():
        setattr(service, field, value)
    if changes := updated(before, snapshot(service, AUDIT_FIELDS)):
        _record(db, audit, service, "service.update", changes)
    await commit_or_raise(db, _name_taken(sent.get("name", service.name)))
    return await get_service(db, workspace_id=workspace_id, service_id=service_id)


async def delete_service(
    db: AsyncSession, workspace_id: uuid.UUID, *, service_id: uuid.UUID, audit: AuditContext
) -> None:
    """Delete a service and its targets. Refused (409) while routes still point to it."""
    service = await get_service(db, workspace_id=workspace_id, service_id=service_id)
    _record(db, audit, service, "service.delete", deleted(snapshot(service, AUDIT_FIELDS)))
    await db.delete(service)
    await commit_or_raise(
        db, {FK_ROUTE_SERVICE: ConflictError("Service still has routes. Delete its routes first.")}
    )


def _record(
    db: AsyncSession, audit_ctx: AuditContext, service: Service, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=service.workspace_id,
        action=action,
        target_type="service",
        target_id=service.id,
        target_name=service.name,
        changes=changes,
    )


# ---- Targets


async def list_targets(
    db: AsyncSession, workspace_id: uuid.UUID, *, service_id: uuid.UUID, page: PageParams
) -> tuple[list[ServiceTarget], int]:
    """One page of a service's targets (oldest first, the order the gateway uses)."""
    await get_service(db, workspace_id=workspace_id, service_id=service_id)  # 404 if not ours
    query = (
        select(ServiceTarget)
        .where(ServiceTarget.workspace_id == workspace_id, ServiceTarget.service_id == service_id)
        .order_by(ServiceTarget.created_at, ServiceTarget.id)
    )
    return await paginate(db, query, page)


async def create_target(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    service_id: uuid.UUID,
    data: TargetCreate,
    audit: AuditContext,
) -> ServiceTarget:
    """Add a target. At most MAX_TARGETS_PER_SERVICE per service (409)."""
    await get_service(db, workspace_id=workspace_id, service_id=service_id, for_update=True)
    if await _count_targets(db, service_id=service_id) >= MAX_TARGETS_PER_SERVICE:
        await db.rollback()  # release the lock
        raise ConflictError(
            f"A service can have at most {MAX_TARGETS_PER_SERVICE} targets. Remove one first."
        )
    target = ServiceTarget(
        id=uuid.uuid4(), workspace_id=workspace_id, service_id=service_id, **data.model_dump()
    )
    db.add(target)
    _record_target(
        db, audit, target, "target.create", created(snapshot(target, TARGET_AUDIT_FIELDS))
    )
    await commit_or_raise(db, _URL_TAKEN)
    await db.refresh(target)  # created_at, updated_at
    return target


async def update_target(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    service_id: uuid.UUID,
    target_id: uuid.UUID,
    data: TargetUpdate,
    audit: AuditContext,
) -> ServiceTarget:
    """Change a target's weight or switch it on or off. A service whose
    targets are all off isn't routable (its routes leave the gateway's config)."""
    target = await _get_target(db, workspace_id, service_id=service_id, target_id=target_id)
    before = snapshot(target, TARGET_AUDIT_FIELDS)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(target, field, value)
    if changes := updated(before, snapshot(target, TARGET_AUDIT_FIELDS)):
        _record_target(db, audit, target, "target.update", changes)
    await commit_or_raise(db, {})
    await db.refresh(target)  # updated_at is set by the database
    return target


async def delete_target(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    service_id: uuid.UUID,
    target_id: uuid.UUID,
    audit: AuditContext,
) -> None:
    """Remove a target. A service keeps at least one (409): switch it off
    instead, or delete the service."""
    await get_service(db, workspace_id=workspace_id, service_id=service_id, for_update=True)
    target = await _get_target(db, workspace_id, service_id=service_id, target_id=target_id)
    if await _count_targets(db, service_id=service_id) <= 1:
        await db.rollback()  # release the lock
        raise ConflictError(
            "A service keeps at least one target. Add another first, "
            "switch this one off (is_active: false), or delete the service."
        )
    _record_target(
        db, audit, target, "target.delete", deleted(snapshot(target, TARGET_AUDIT_FIELDS))
    )
    await db.delete(target)
    await commit_or_raise(db, {})


async def _get_target(
    db: AsyncSession, workspace_id: uuid.UUID, *, service_id: uuid.UUID, target_id: uuid.UUID
) -> ServiceTarget:
    target = await db.scalar(
        select(ServiceTarget)
        .where(
            ServiceTarget.id == target_id,
            ServiceTarget.service_id == service_id,
            ServiceTarget.workspace_id == workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise NotFoundError("Target not found")
    return target


async def _count_targets(db: AsyncSession, *, service_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(ServiceTarget)
        .where(ServiceTarget.service_id == service_id)
    )
    return count or 0


def _record_target(
    db: AsyncSession,
    audit_ctx: AuditContext,
    target: ServiceTarget,
    action: str,
    changes: Changes,
) -> None:
    if "url" in changes:
        # Services saved before MT.3 may hold credentials in their URL, and
        # the MT.10 migration moved them into targets: keep them out of the log.
        changes["url"] = [_without_credentials(url) for url in changes["url"]]
    audit.record(
        db,
        audit_ctx,
        workspace_id=target.workspace_id,
        action=action,
        target_type="target",
        target_id=target.id,
        target_name=_without_credentials(target.url),
        changes=changes,
    )


def _without_credentials(url: str | None) -> str | None:
    if url is None:
        return None
    parts = urlsplit(url)
    return parts._replace(netloc=parts.netloc.rpartition("@")[2]).geturl()
