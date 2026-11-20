"""Dependencies shared by every feature.

Who may call what:
- CurrentUser: any signed-in, active person (a valid GoTrue access token in
  `Authorization: Bearer <jwt>`).
      async def me(user: CurrentUser): ...
- PlatformAdmin: a Platform Admin whose token carries MFA (aal2). For the
  platform endpoints (PLAN.md section 6).
- WorkspaceId: the workspace an endpoint acts on (see get_workspace_id).
- InternalOnly: /internal endpoints need `X-Internal-Token`, the secret
  shared with the gateway (a user's JWT does NOT open them).
      @router.get("/config", dependencies=[InternalOnly])
"""

import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ForbiddenError
from app.core.identity import TokenClaims, verify_access_token
from app.gotrue.client import GoTrueAdmin
from app.users import crud as users_crud
from app.users.models import User
from app.workspaces import crud as workspaces_crud

DbSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False: we raise 401 ourselves. (By default FastAPI would answer
# 403 when the header is missing, but "not signed in" is a 401.)
# It also adds the "Authorize" button to Swagger.
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(frozen=True)
class Caller:
    """Who is calling (our row) and what their token proves (e.g. MFA)."""

    user: User
    claims: TokenClaims


async def get_caller(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> Caller:
    """Step 1 of every request (PLAN.md section 6): verify the token, find the person.

    `is_active` is read from the database on every request, so disabling a
    person takes effect on their very next call, token or not.
    """
    if credentials is None:
        raise _unauthorized()
    claims = verify_access_token(credentials.credentials)
    if claims is None:
        raise _unauthorized()
    user = await users_crud.get_or_create_from_token(db, claims)
    if not user.is_active:
        raise ForbiddenError("user_disabled")
    return Caller(user=user, claims=claims)


CurrentCaller = Annotated[Caller, Depends(get_caller)]


async def get_current_user(caller: CurrentCaller) -> User:
    return caller.user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_platform_admin(caller: CurrentCaller) -> User:
    """Platform endpoints: Platform Admins only, and only with MFA (decision 12).

    This account reaches every workspace, so a stolen password alone must not
    be enough: a token without aal2 gets 403 mfa_required.
    """
    if not caller.user.is_platform_admin:
        raise ForbiddenError("platform_admin_required")
    if not caller.claims.has_mfa:
        raise ForbiddenError("mfa_required")
    return caller.user


PlatformAdmin = Annotated[User, Depends(require_platform_admin)]


async def get_workspace_id(user: CurrentUser, db: DbSession) -> uuid.UUID:
    """The workspace a service, route, consumer or key endpoint acts on.

    MT.1 bridge, until MT.2 adds memberships and `/workspaces/{slug}` URLs:
    every such endpoint acts on `default`, and only Platform Admins may call
    them (the migrated MVP admin is one). Their MFA is not required here,
    because in MT.2 they become the Owner of `default`, and Owners don't need it.
    """
    if not user.is_platform_admin:
        raise ForbiddenError("platform_admin_required")
    return await workspaces_crud.get_default_workspace_id(db)


WorkspaceId = Annotated[uuid.UUID, Depends(get_workspace_id)]


def get_gotrue(request: Request) -> GoTrueAdmin:
    """The app's one GoTrue admin client, made at startup (see main.lifespan)."""
    gotrue: GoTrueAdmin = request.app.state.gotrue
    return gotrue


GoTrue = Annotated[GoTrueAdmin, Depends(get_gotrue)]


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


InternalOnly = Depends(require_internal_token)
