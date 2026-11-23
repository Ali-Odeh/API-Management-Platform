"""All database work for plans, always scoped to one workspace (V2.2).

The rules (PLAN.md section 6, "Plans and quotas"):
- a plan's rate limit is never above the workspace's effective limit: one
  consumer could never use it (422);
- a workspace has exactly one default plan: making another plan the default
  moves it there, and the default can't be unset or deleted;
- a plan that consumers are on can't be deleted (409).

Writes run with the workspace row locked (require_permission locks it for
every write), so the workspace's limit can't change between the check and
the commit, and two requests can't move the default at the same time.
"""

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import crud as audit
from app.audit.changes import Changes, created, deleted, snapshot, updated
from app.audit.context import AuditContext
from app.consumers.models import Consumer
from app.core.db import commit_or_raise
from app.core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from app.core.pagination import PageParams, paginate
from app.plans.models import (
    DEFAULT_PLAN_NAME,
    DEFAULT_PLAN_RATE_LIMIT_PER_MIN,
    UQ_PLAN_NAME,
    Plan,
)
from app.plans.schemas import PlanCreate, PlanUpdate
from app.workspaces.limits import effective_limits
from app.workspaces.models import Workspace

AUDIT_FIELDS = ("name", "rate_limit_per_min", "monthly_quota", "is_default")
PLAN_NOT_FOUND = "Plan not found"
DEFAULT_PLAN = "default_plan"  # 409: the default plan can't be deleted
PLAN_IN_USE = "plan_in_use"  # 409: consumers are on it
# The consumers' foreign key to plans (see the "plans" migration).
FK_CONSUMER_PLAN = "fk_consumers_plan"


def _errors(name: str) -> dict[str, AppError]:
    return {
        UQ_PLAN_NAME: ConflictError(f"A plan named '{name}' already exists"),
        FK_CONSUMER_PLAN: ConflictError(PLAN_IN_USE),  # a consumer moved onto it meanwhile
    }


def add_default_plan(db: AsyncSession, workspace_id: uuid.UUID) -> Plan:
    """The plan a new workspace starts with (part of creating the workspace,
    which records one event for everything)."""
    plan = Plan(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=DEFAULT_PLAN_NAME,
        rate_limit_per_min=DEFAULT_PLAN_RATE_LIMIT_PER_MIN,
        is_default=True,
    )
    db.add(plan)
    return plan


async def list_plans(
    db: AsyncSession, workspace_id: uuid.UUID, *, page: PageParams
) -> tuple[list[Plan], int]:
    """One page of plans, the default first, then by rate limit."""
    query = (
        select(Plan)
        .where(Plan.workspace_id == workspace_id)
        .order_by(Plan.is_default.desc(), Plan.rate_limit_per_min, Plan.name)
    )
    return await paginate(db, query, page)


async def get_plan(db: AsyncSession, workspace_id: uuid.UUID, *, plan_id: uuid.UUID) -> Plan:
    plan = await db.scalar(
        select(Plan).where(Plan.id == plan_id, Plan.workspace_id == workspace_id)
    )
    if plan is None:
        raise NotFoundError(PLAN_NOT_FOUND)
    return plan


async def get_default_plan(db: AsyncSession, workspace_id: uuid.UUID) -> Plan:
    """The workspace's default plan (there always is one)."""
    return (
        await db.scalars(select(Plan).where(Plan.workspace_id == workspace_id, Plan.is_default))
    ).one()


async def plan_for_consumer(
    db: AsyncSession, workspace_id: uuid.UUID, plan_id: uuid.UUID | None
) -> Plan:
    """The plan a consumer is put on: the named one (422 if it isn't in this
    workspace, as for every reference in a body), else the default."""
    if plan_id is None:
        return await get_default_plan(db, workspace_id)
    try:
        return await get_plan(db, workspace_id, plan_id=plan_id)
    except NotFoundError:
        raise UnprocessableError(PLAN_NOT_FOUND) from None


async def create_plan(
    db: AsyncSession, workspace_id: uuid.UUID, *, data: PlanCreate, audit: AuditContext
) -> Plan:
    await _check_within_workspace(db, workspace_id, data.rate_limit_per_min)
    if data.is_default:
        await _clear_default(db, workspace_id)
    plan = Plan(id=uuid.uuid4(), workspace_id=workspace_id, **data.model_dump())
    db.add(plan)
    _record(db, audit, plan, "plan.create", created(snapshot(plan, AUDIT_FIELDS)))
    await commit_or_raise(db, _errors(data.name))
    await db.refresh(plan)
    return plan


async def update_plan(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    plan_id: uuid.UUID,
    data: PlanUpdate,
    audit: AuditContext,
) -> Plan:
    plan = await get_plan(db, workspace_id, plan_id=plan_id)
    sent: dict[str, Any] = data.model_dump(exclude_unset=True)
    if sent.get("is_default") is False and plan.is_default:
        raise UnprocessableError(
            "A workspace always has a default plan: make another plan the default instead"
        )
    if "rate_limit_per_min" in sent:
        await _check_within_workspace(db, workspace_id, sent["rate_limit_per_min"])
    if sent.get("is_default") and not plan.is_default:
        await _clear_default(db, workspace_id)
    before = snapshot(plan, AUDIT_FIELDS)
    for field, value in sent.items():
        setattr(plan, field, value)
    if changes := updated(before, snapshot(plan, AUDIT_FIELDS)):
        _record(db, audit, plan, "plan.update", changes)
    await commit_or_raise(db, _errors(plan.name))
    await db.refresh(plan)
    return plan


async def delete_plan(
    db: AsyncSession, workspace_id: uuid.UUID, *, plan_id: uuid.UUID, audit: AuditContext
) -> None:
    plan = await get_plan(db, workspace_id, plan_id=plan_id)
    if plan.is_default:
        raise ConflictError(DEFAULT_PLAN)
    on_it = await db.scalar(
        select(func.count()).where(
            Consumer.workspace_id == workspace_id, Consumer.plan_id == plan.id
        )
    )
    if on_it:
        raise ConflictError(PLAN_IN_USE)
    _record(db, audit, plan, "plan.delete", deleted(snapshot(plan, AUDIT_FIELDS)))
    await db.delete(plan)
    await commit_or_raise(db, _errors(plan.name))


async def _check_within_workspace(db: AsyncSession, workspace_id: uuid.UUID, rate: int) -> None:
    """422 when the plan's rate limit is above the workspace's own (effective) limit."""
    workspace = (await db.scalars(select(Workspace).where(Workspace.id == workspace_id))).one()
    ceiling = effective_limits(workspace)["rate_limit_per_min"]
    if rate > ceiling:
        raise UnprocessableError(
            f"rate_limit_per_min is above the workspace's limit of {ceiling} requests per minute"
        )


async def _clear_default(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Unset the current default, before another plan takes it (the unique
    index allows one default per workspace)."""
    await db.execute(
        update(Plan)
        .where(Plan.workspace_id == workspace_id, Plan.is_default)
        .values(is_default=False)
        .execution_options(synchronize_session="fetch")
    )


def _record(
    db: AsyncSession, audit_ctx: AuditContext, plan: Plan, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=plan.workspace_id,
        action=action,
        target_type="plan",
        target_id=plan.id,
        target_name=plan.name,
        changes=changes,
    )
