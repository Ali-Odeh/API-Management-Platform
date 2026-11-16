"""Dependencies shared by every feature.

get_current_admin is the "lock": put it on an endpoint and only a request
with a valid `Authorization: Bearer <token>` header gets through.

    @router.get("/services")
    async def list_services(admin: Admin = Depends(get_current_admin)): ...
"""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import crud as auth_crud
from app.auth.models import Admin
from app.core.db import get_db
from app.core.security import decode_access_token

# auto_error=False: we raise 401 ourselves. (By default FastAPI would answer
# 403 when the header is missing, but "not logged in" is a 401.)
# It also adds the "Authorize" button to Swagger.
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Admin:
    if credentials is None:
        raise _unauthorized()

    subject = decode_access_token(credentials.credentials)  # None if bad/expired
    if subject is None:
        raise _unauthorized()

    try:
        admin_id = uuid.UUID(subject)
    except ValueError:
        raise _unauthorized() from None

    admin = await auth_crud.get_admin_by_id(db, admin_id)
    if admin is None:  # token is valid but the admin was deleted
        raise _unauthorized()
    return admin


# Short aliases so endpoints read nicely:  admin: CurrentAdmin, db: DbSession
CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
