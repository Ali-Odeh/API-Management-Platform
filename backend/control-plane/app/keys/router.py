"""HTTP layer for API keys: parse input, call crud, shape output. No SQL here.

Two URL shapes, as in the plan: keys are created and listed under their
consumer, and revoked by their own id.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Response, status

from app.core.deps import DbSession, WorkspaceAccess, require_permission
from app.core.openapi import error_responses
from app.core.pagination import Page, Pagination
from app.core.permissions import Permission
from app.keys import crud
from app.keys.schemas import KeyCreate, KeyCreated, KeyOut

router = APIRouter(prefix="/api/v1/workspaces/{slug}", tags=["keys"])

CanRead = Annotated[WorkspaceAccess, require_permission(Permission.KEYS_READ)]
CanWrite = Annotated[WorkspaceAccess, require_permission(Permission.KEYS_WRITE)]

_CONSUMER_NOT_FOUND = error_responses({404: "Consumer not found"})
_TOO_MANY = error_responses({409: "Consumer already has the maximum number of active keys"})
_KEY_NOT_FOUND = error_responses({404: "API key not found"})


@router.get("/consumers/{consumer_id}/keys", responses=_CONSUMER_NOT_FOUND)
async def list_keys(
    consumer_id: uuid.UUID, access: CanRead, db: DbSession, page: Pagination
) -> Page[KeyOut]:
    keys, total = await crud.list_keys(
        db, workspace_id=access.workspace_id, consumer_id=consumer_id, page=page
    )
    return Page[KeyOut](items=[KeyOut.model_validate(k) for k in keys], total=total)


@router.post(
    "/consumers/{consumer_id}/keys",
    status_code=status.HTTP_201_CREATED,
    responses=_CONSUMER_NOT_FOUND | _TOO_MANY,
)
async def create_key(
    consumer_id: uuid.UUID,
    body: KeyCreate,
    access: CanWrite,
    db: DbSession,
    response: Response,
) -> KeyCreated:
    """Generate a key. **The `key` field is shown only in this response.**"""
    key, raw_key = await crud.create_key(
        db, workspace_id=access.workspace_id, audit=access.audit, consumer_id=consumer_id, data=body
    )
    # A secret must never be stored by a browser or proxy cache.
    response.headers["Cache-Control"] = "no-store"
    return KeyCreated(**KeyOut.model_validate(key).model_dump(), key=raw_key)


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_KEY_NOT_FOUND)
async def revoke_key(key_id: uuid.UUID, access: CanWrite, db: DbSession) -> None:
    """Revoke a key. It stays in the list as revoked; revoking again changes nothing."""
    await crud.revoke_key(db, workspace_id=access.workspace_id, audit=access.audit, key_id=key_id)
