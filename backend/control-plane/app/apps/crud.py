"""All database work for apps (P.2): developers make them, members decide on them.

The rules (PLAN.md section 6, "Developer portal API" and "Apps"):
- an app is made in an active workspace, for one of its published APIs
  (deprecated: 409 api_deprecated), on a plan the workspace shows
  developers; anything else named in the body is 422, as everywhere;
- its consumer is made with it, on that plan; a plan that requires
  approval makes the app `pending` and its consumer blocked;
- a consumer is active exactly while its app is approved and its developer
  isn't blocked (see `consumer_status`);
- a developer reaches only their own apps (404 otherwise), and writes
  nothing in a suspended workspace (403 workspace_suspended);
- one developer has at most MAX_APPS_PER_DEVELOPER apps per workspace, and
  every app counts against the workspace's consumers quota.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis.models import Api, ApiStatus
from app.apps.models import MAX_APPS_PER_DEVELOPER, UQ_APP_NAME, App, AppStatus
from app.apps.schemas import AppCreate, AppDecision, Quota
from app.audit import crud as audit
from app.audit.changes import Changes, created, deleted, snapshot, updated
from app.audit.context import AuditContext
from app.consumers.models import Consumer, ConsumerStatus
from app.core.db import commit_or_raise
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from app.core.pagination import PageParams
from app.developers.models import Developer, DeveloperStatus
from app.keys import crud as keys_crud
from app.keys.models import ApiKey
from app.keys.schemas import KeyCreate
from app.plans.models import Plan
from app.usage.models import RequestLog
from app.workspaces.crud import WORKSPACE_NOT_FOUND
from app.workspaces.limits import QUOTA_EXCEEDED, check_quota
from app.workspaces.models import Workspace, WorkspaceStatus

APP_NOT_FOUND = "App not found"
API_NOT_FOUND = "API not found"
PLAN_NOT_FOUND = "Plan not found"
AUDIT_FIELDS = ("name", "status", "api_id", "consumer_id")
# What the gateway logs for a request its limits let through (and so a
# request that spent the monthly quota).
_LET_THROUGH = "ok"


@dataclass(frozen=True)
class AppView:
    """An app with what its answers show: workspace, API, plan and developer."""

    app: App
    workspace_slug: str
    workspace_name: str
    workspace_status: WorkspaceStatus
    api: Api
    plan: Plan
    developer_email: str


def consumer_status(app_status: AppStatus, developer_status: DeveloperStatus) -> ConsumerStatus:
    """The one rule that decides whether an app's keys work at the gateway."""
    if app_status.consumer_active and developer_status == DeveloperStatus.ACTIVE:
        return ConsumerStatus.ACTIVE
    return ConsumerStatus.BLOCKED


def _views() -> Select[Any]:
    return (
        select(App, Workspace.slug, Workspace.name, Workspace.status, Api, Plan, Developer.email)
        .join(Workspace, Workspace.id == App.workspace_id)
        .join(Api, (Api.id == App.api_id) & (Api.workspace_id == App.workspace_id))
        .join(
            Consumer,
            (Consumer.id == App.consumer_id) & (Consumer.workspace_id == App.workspace_id),
        )
        .join(Plan, (Plan.id == Consumer.plan_id) & (Plan.workspace_id == App.workspace_id))
        .join(Developer, Developer.id == App.developer_id)
    )


def _view(row: Any) -> AppView:
    return AppView(*row)


async def _page(
    db: AsyncSession, query: Select[Any], page: PageParams
) -> tuple[list[AppView], int]:
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = await db.execute(
        query.order_by(App.created_at.desc(), App.id).offset(page.offset).limit(page.page_size)
    )
    return [_view(row) for row in rows], total or 0


# ---- The developer's side (portal)


async def list_developer_apps(
    db: AsyncSession, developer_id: uuid.UUID, *, page: PageParams
) -> tuple[list[AppView], int]:
    """A developer's own apps, newest first, in workspaces that still exist."""
    query = _views().where(
        App.developer_id == developer_id,
        Workspace.status.in_((WorkspaceStatus.ACTIVE, WorkspaceStatus.SUSPENDED)),
    )
    return await _page(db, query, page)


async def get_developer_app(
    db: AsyncSession, developer_id: uuid.UUID, *, app_id: uuid.UUID
) -> AppView:
    """One of the developer's own apps; another developer's is 404, like none."""
    row = (
        await db.execute(
            _views().where(
                App.id == app_id,
                App.developer_id == developer_id,
                Workspace.status.in_((WorkspaceStatus.ACTIVE, WorkspaceStatus.SUSPENDED)),
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(APP_NOT_FOUND)
    return _view(row)


async def create_app(
    db: AsyncSession, developer: Developer, *, data: AppCreate, audit_ctx: AuditContext
) -> AppView:
    workspace_id = await _active_workspace(db, data.workspace)
    await _published_api(db, workspace_id, data.api_id)
    plan = await _offered_plan(db, workspace_id, data.plan_id)
    await check_quota(db, workspace_id, "max_consumers")  # also locks the workspace row
    mine = await db.scalar(
        select(func.count()).where(
            App.workspace_id == workspace_id, App.developer_id == developer.id
        )
    )
    if (mine or 0) >= MAX_APPS_PER_DEVELOPER:
        raise ConflictError(QUOTA_EXCEEDED)

    status = AppStatus.PENDING if plan.requires_approval else AppStatus.APPROVED
    app = App(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        developer_id=developer.id,
        api_id=data.api_id,
        name=data.name,
        status=status,
    )
    consumer = Consumer(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=f"app-{app.id}",
        email=developer.email,
        plan_id=plan.id,
        status=consumer_status(status, developer.status),
    )
    app.consumer_id = consumer.id
    db.add(consumer)
    await db.flush()  # the consumer first: the app points at it
    db.add(app)
    changes = created(snapshot(app, AUDIT_FIELDS)) | {"plan_id": [None, str(plan.id)]}
    _record(db, audit_ctx, app, "app.create", changes)
    await commit_or_raise(
        db, {UQ_APP_NAME: ConflictError(f"You already have an app named '{data.name}' here")}
    )
    return await get_developer_app(db, developer.id, app_id=app.id)


async def delete_developer_app(
    db: AsyncSession, developer_id: uuid.UUID, *, app_id: uuid.UUID, audit_ctx: AuditContext
) -> None:
    """Delete an app with its consumer and keys: its keys stop working at once."""
    view = _writable(await get_developer_app(db, developer_id, app_id=app_id))
    _record(db, audit_ctx, view.app, "app.delete", deleted(snapshot(view.app, AUDIT_FIELDS)))
    consumer = await db.get_one(Consumer, view.app.consumer_id)
    await db.delete(consumer)  # the database deletes the app and the keys with it
    await db.commit()


async def list_app_keys(
    db: AsyncSession, developer_id: uuid.UUID, *, app_id: uuid.UUID, page: PageParams
) -> tuple[list[ApiKey], int]:
    view = await get_developer_app(db, developer_id, app_id=app_id)
    return await keys_crud.list_keys(
        db, view.app.workspace_id, consumer_id=view.app.consumer_id, page=page
    )


async def create_app_key(
    db: AsyncSession,
    developer_id: uuid.UUID,
    *,
    app_id: uuid.UUID,
    data: KeyCreate,
    audit_ctx: AuditContext,
) -> tuple[ApiKey, str]:
    """A key for an approved app (the admin API's key rules: at most 10 active)."""
    view = _writable(await get_developer_app(db, developer_id, app_id=app_id))
    if view.app.status != AppStatus.APPROVED:
        raise ConflictError(f"app_{view.app.status.value}")
    return await keys_crud.create_key(
        db,
        view.app.workspace_id,
        consumer_id=view.app.consumer_id,
        data=data,
        audit=audit_ctx,
    )


async def revoke_app_key(
    db: AsyncSession,
    developer_id: uuid.UUID,
    *,
    app_id: uuid.UUID,
    key_id: uuid.UUID,
    audit_ctx: AuditContext,
) -> None:
    view = _writable(await get_developer_app(db, developer_id, app_id=app_id))
    await keys_crud.revoke_key(
        db,
        view.app.workspace_id,
        key_id=key_id,
        consumer_id=view.app.consumer_id,
        audit=audit_ctx,
    )


async def quota_this_month(
    db: AsyncSession, view: AppView, *, now: datetime | None = None
) -> Quota:
    """The app's monthly quota and how much of it the request log shows used."""
    now = now or datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    resets_at = (month_start + timedelta(days=32)).replace(day=1)
    used = await db.scalar(
        select(func.count()).where(
            RequestLog.workspace_id == view.app.workspace_id,
            RequestLog.consumer_id == view.app.consumer_id,
            RequestLog.ts >= month_start,
            RequestLog.ts < resets_at,
            RequestLog.ratelimit == _LET_THROUGH,
        )
    )
    limit = view.plan.monthly_quota
    used = used or 0
    return Quota(
        monthly_quota=limit,
        used=used,
        remaining=max(limit - used, 0) if limit is not None else None,
        resets_at=resets_at,
    )


async def _active_workspace(db: AsyncSession, slug: str) -> uuid.UUID:
    workspace_id = await db.scalar(
        select(Workspace.id).where(
            Workspace.slug == slug, Workspace.status == WorkspaceStatus.ACTIVE
        )
    )
    if workspace_id is None:
        raise UnprocessableError(WORKSPACE_NOT_FOUND)
    return workspace_id


async def _published_api(db: AsyncSession, workspace_id: uuid.UUID, api_id: uuid.UUID) -> None:
    """The API an app is made for: published (a deprecated one takes no new apps)."""
    status = await db.scalar(
        select(Api.status).where(Api.id == api_id, Api.workspace_id == workspace_id)
    )
    if status is None or status == ApiStatus.DRAFT:
        raise UnprocessableError(API_NOT_FOUND)
    if status == ApiStatus.DEPRECATED:
        raise ConflictError("api_deprecated")


async def _offered_plan(db: AsyncSession, workspace_id: uuid.UUID, plan_id: uuid.UUID) -> Plan:
    plan = await db.scalar(
        select(Plan).where(
            Plan.id == plan_id, Plan.workspace_id == workspace_id, Plan.portal_visible
        )
    )
    if plan is None:
        raise UnprocessableError(PLAN_NOT_FOUND)
    return plan


def _writable(view: AppView) -> AppView:
    """A suspended workspace is read-only for developers too."""
    if view.workspace_status != WorkspaceStatus.ACTIVE:
        raise ForbiddenError("workspace_suspended")
    return view


# ---- The workspace's side (members, apps:read / apps:write)


async def list_apps(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    page: PageParams,
    status: AppStatus | None = None,
) -> tuple[list[AppView], int]:
    """The workspace's apps, newest first; `status=pending` is the approval queue."""
    query = _views().where(App.workspace_id == workspace_id)
    if status is not None:
        query = query.where(App.status == status)
    return await _page(db, query, page)


async def get_app(db: AsyncSession, workspace_id: uuid.UUID, *, app_id: uuid.UUID) -> AppView:
    row = (
        await db.execute(_views().where(App.id == app_id, App.workspace_id == workspace_id))
    ).one_or_none()
    if row is None:
        raise NotFoundError(APP_NOT_FOUND)
    return _view(row)


async def decide(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    app_id: uuid.UUID,
    data: AppDecision,
    audit_ctx: AuditContext,
) -> AppView:
    """Approve or reject an app; its consumer follows (see consumer_status)."""
    app = await db.scalar(
        select(App)
        .where(App.id == app_id, App.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if app is None:
        raise NotFoundError(APP_NOT_FOUND)
    wanted = AppStatus(data.status.value) if data.status is not None else app.status
    if app.status != wanted:
        before = snapshot(app, AUDIT_FIELDS)
        app.status = wanted
        developer_status = await db.scalar(
            select(Developer.status).where(Developer.id == app.developer_id)
        )
        assert developer_status is not None  # the foreign key keeps the developer
        await db.execute(
            update(Consumer)
            .where(Consumer.id == app.consumer_id, Consumer.workspace_id == workspace_id)
            .values(status=consumer_status(app.status, developer_status))
        )
        _record(db, audit_ctx, app, "app.update", updated(before, snapshot(app, AUDIT_FIELDS)))
    await db.commit()
    return await get_app(db, workspace_id, app_id=app_id)


async def follow_developer(
    db: AsyncSession, developer_id: uuid.UUID, status: DeveloperStatus
) -> None:
    """A developer blocked or unblocked: the consumers of their approved apps follow,
    in every workspace (in the caller's transaction)."""
    approved = select(App.consumer_id).where(
        App.developer_id == developer_id, App.status == AppStatus.APPROVED
    )
    await db.execute(
        update(Consumer)
        .where(Consumer.id.in_(approved))
        .values(status=consumer_status(AppStatus.APPROVED, status))
        .execution_options(synchronize_session=False)
    )


def _record(
    db: AsyncSession, audit_ctx: AuditContext, app: App, action: str, changes: Changes
) -> None:
    audit.record(
        db,
        audit_ctx,
        workspace_id=app.workspace_id,
        action=action,
        target_type="app",
        target_id=app.id,
        target_name=app.name,
        changes=changes,
    )
