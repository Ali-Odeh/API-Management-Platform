"""The developer portal API, part two (P.2): a developer's own apps, keys and usage.

A developer sees only their own apps: another developer's app, or one in a
deleted workspace, is the same 404 as one that doesn't exist. Every change
is recorded in the app's workspace log with `actor_type` `developer`.
"""

import uuid

from fastapi import APIRouter, Response, status

from app.apps import crud
from app.apps.crud import AppView
from app.apps.schemas import ApiRef, AppCreate, AppOut, AppUsage, PlanRef, WorkspaceRef
from app.core.deps import DbSession
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.keys.schemas import KeyCreate, KeyCreated, KeyOut
from app.portal.deps import CurrentDeveloper
from app.usage import crud as usage_crud
from app.usage.schemas import Interval, Range

router = APIRouter(prefix="/api/v1/portal/apps", tags=["portal"])

_NOT_FOUND = error_responses({404: "App not found"})
_WRITE = error_responses(
    {
        403: "workspace_suspended: the app's workspace is read-only",
        404: "App not found",
    }
)


def app_out(view: AppView) -> AppOut:
    return AppOut(
        id=view.app.id,
        name=view.app.name,
        status=view.app.status,
        workspace=WorkspaceRef(slug=view.workspace_slug, name=view.workspace_name),
        api=ApiRef.model_validate(view.api),
        plan=PlanRef.model_validate(view.plan),
        created_at=view.app.created_at,
    )


@router.get("")
async def list_apps(caller: CurrentDeveloper, db: DbSession, page: Pagination) -> Page[AppOut]:
    """My apps, in every workspace, newest first."""
    views, total = await crud.list_developer_apps(db, caller.developer.id, page=page)
    return Page[AppOut](items=[app_out(v) for v in views], total=total)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        {
            409: "api_deprecated, quota_exceeded (the workspace's consumers, or "
            "10 apps of yours in it) or a name you already use there",
            422: "A workspace, API or plan that isn't offered (unknown, a draft, "
            "a plan not shown to developers)",
        }
    ),
)
async def create_app(body: AppCreate, caller: CurrentDeveloper, db: DbSession) -> AppOut:
    """A new app. On a plan that requires approval it waits (`pending`) for an API
    Admin; otherwise it is `approved` and can get keys at once."""
    view = await crud.create_app(db, caller.developer, data=body, audit_ctx=caller.audit)
    return app_out(view)


@router.get("/{app_id}", responses=_NOT_FOUND)
async def get_app(app_id: uuid.UUID, caller: CurrentDeveloper, db: DbSession) -> AppOut:
    return app_out(await crud.get_developer_app(db, caller.developer.id, app_id=app_id))


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_WRITE)
async def delete_app(app_id: uuid.UUID, caller: CurrentDeveloper, db: DbSession) -> None:
    """Delete an app and its keys: they stop working at once."""
    await crud.delete_developer_app(db, caller.developer.id, app_id=app_id, audit_ctx=caller.audit)


@router.get("/{app_id}/keys", responses=_NOT_FOUND)
async def list_keys(
    app_id: uuid.UUID, caller: CurrentDeveloper, db: DbSession, page: Pagination
) -> Page[KeyOut]:
    keys, total = await crud.list_app_keys(db, caller.developer.id, app_id=app_id, page=page)
    return Page[KeyOut](items=[KeyOut.model_validate(k) for k in keys], total=total)


@router.post(
    "/{app_id}/keys",
    status_code=status.HTTP_201_CREATED,
    responses=_WRITE
    | error_responses({409: "app_pending or app_rejected, or already the maximum of active keys"}),
)
async def create_key(
    app_id: uuid.UUID,
    body: KeyCreate,
    caller: CurrentDeveloper,
    db: DbSession,
    response: Response,
) -> KeyCreated:
    """Generate a key for an approved app. **The `key` field is shown only in this response.**"""
    key, raw_key = await crud.create_app_key(
        db, caller.developer.id, app_id=app_id, data=body, audit_ctx=caller.audit
    )
    response.headers["Cache-Control"] = "no-store"
    return KeyCreated(**KeyOut.model_validate(key).model_dump(), key=raw_key)


@router.delete(
    "/{app_id}/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses({404: "App or API key not found"}) | _WRITE,
)
async def revoke_key(
    app_id: uuid.UUID, key_id: uuid.UUID, caller: CurrentDeveloper, db: DbSession
) -> None:
    """Revoke a key of the app. Revoking again changes nothing."""
    await crud.revoke_app_key(
        db, caller.developer.id, app_id=app_id, key_id=key_id, audit_ctx=caller.audit
    )


@router.get("/{app_id}/usage", responses=_NOT_FOUND)
async def usage(
    app_id: uuid.UUID,
    caller: CurrentDeveloper,
    db: DbSession,
    time: Range,
    interval: Interval = Interval.HOUR,
) -> AppUsage:
    """The app's requests over time (from the request log) and its monthly quota."""
    view = await crud.get_developer_app(db, caller.developer.id, app_id=app_id)
    traffic = await usage_crud.traffic(
        db, view.app.workspace_id, time=time, interval=interval, consumer_id=view.app.consumer_id
    )
    return AppUsage(traffic=traffic, quota=await crud.quota_this_month(db, view))
