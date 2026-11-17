"""Dependencies shared by every feature.

Two locks:
- get_current_admin: admin endpoints need `Authorization: Bearer <JWT>`.
      async def list_services(admin: CurrentAdmin): ...
- require_internal_token: /internal endpoints need `X-Internal-Token`, the
  secret shared with the gateway (an admin JWT does NOT open them).
      @router.get("/config", dependencies=[InternalOnly])
"""

import secrets
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import crud as auth_crud
from app.auth.models import Admin
from app.core.config import get_settings
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


# Also adds an "X-Internal-Token" field to Swagger's Authorize dialog.
internal_token_scheme = APIKeyHeader(name="X-Internal-Token", auto_error=False)


async def require_internal_token(
    token: Annotated[str | None, Depends(internal_token_scheme)],
) -> None:
    """Only the gateway knows INTERNAL_TOKEN.

    compare_digest takes the same time whether the first or the last character
    differs, so response times can't reveal the secret letter by letter.
    """
    expected = get_settings().internal_token
    if token is None or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token"
        )


# Short aliases so endpoints read nicely:  admin: CurrentAdmin, db: DbSession
CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
InternalOnly = Depends(require_internal_token)
