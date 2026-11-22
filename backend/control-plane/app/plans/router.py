"""HTTP layer for plans: parse input, call crud, shape output. No SQL here."""

import uuid
from typing import Annotated

from fastapi import APIRouter, status

from app.core.deps import DbSession, WorkspaceAccess, require_permission
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission
from app.plans import crud
from app.plans.schemas import PlanCreate, PlanOut, PlanUpdate

router = APIRouter(prefix="/api/v1/workspaces/{slug}/plans", tags=["plans"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.PLANS_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.PLANS_WRITE)]

_NOT_FOUND = error_responses({404: "Plan not found"})
_CONFLICT = error_responses({409: "Name already used in this workspace"})
_ABOVE = error_responses({422: "rate_limit_per_min above the workspace's own limit"})
_DELETE_CONFLICT = error_responses(
    {409: "default_plan (move the default first) or plan_in_use (consumers are on it)"}
)


@router.get("")
async def list_plans(access: CanRead, db: DbSession, page: Pagination) -> Page[PlanOut]:
    """The workspace's plans, the default first."""
    plans, total = await crud.list_plans(db, access.workspace_id, page=page)
    return Page[PlanOut](items=[PlanOut.model_validate(p) for p in plans], total=total)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CONFLICT | _ABOVE)
async def create_plan(body: PlanCreate, access: CanWrite, db: DbSession) -> PlanOut:
    plan = await crud.create_plan(db, access.workspace_id, data=body, audit=access.audit)
    return PlanOut.model_validate(plan)


@router.get("/{plan_id}", responses=_NOT_FOUND)
async def get_plan(plan_id: uuid.UUID, access: CanRead, db: DbSession) -> PlanOut:
    return PlanOut.model_validate(await crud.get_plan(db, access.workspace_id, plan_id=plan_id))


@router.patch("/{plan_id}", responses=_NOT_FOUND | _CONFLICT | _ABOVE)
async def update_plan(
    plan_id: uuid.UUID, body: PlanUpdate, access: CanWrite, db: DbSession
) -> PlanOut:
    """Changes apply to every consumer on the plan with the next config update."""
    plan = await crud.update_plan(
        db, access.workspace_id, plan_id=plan_id, data=body, audit=access.audit
    )
    return PlanOut.model_validate(plan)


@router.delete(
    "/{plan_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND | _DELETE_CONFLICT
)
async def delete_plan(plan_id: uuid.UUID, access: CanWrite, db: DbSession) -> None:
    await crud.delete_plan(db, access.workspace_id, plan_id=plan_id, audit=access.audit)
