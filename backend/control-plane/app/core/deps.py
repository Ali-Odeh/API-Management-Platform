"""Dependencies shared by every feature.

Who may call what:
- CurrentUser: any signed-in, active person (a valid GoTrue access token in
  `Authorization: Bearer <jwt>`).
      async def me(user: CurrentUser): ...
- PlatformAdmin: a Platform Admin whose token carries MFA (aal2). For the
  platform endpoints (PLAN.md section 6).
- require_permission(...): the workspace in the URL (`/workspaces/{slug}/...`)
  and the caller's right to act there (see PermissionCheck).
- InternalOnly: /internal endpoints need `X-Internal-Token`, the secret
  shared with the gateway (a user's JWT does NOT open them).
      @router.get("/config", dependencies=[InternalOnly])
"""

import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.context import AuditContext
from app.audit.models import ActorType
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError
from app.core.identity import TokenClaims, verify_access_token
from app.core.permissions import Permission, Role, role_allows
from app.gotrue.client import GoTrueAdmin
from app.users import crud as users_crud
from app.users.models import User
from app.workspaces import crud as workspaces_crud
from app.workspaces.crud import WORKSPACE_NOT_FOUND
from app.workspaces.models import WorkspaceStatus

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


def request_audit(request: Request, actor_type: ActorType, user: User | None) -> AuditContext:
    """What an audit event records about this request and who made it."""
    return AuditContext(
        actor_type=actor_type,
        actor_user_id=user.id if user is not None else None,
        actor_email=user.email if user is not None else None,
        request_id=getattr(request.state, "request_id", None),
        ip=request.client.host if request.client else None,
        user_agent=AuditContext.clip_user_agent(request.headers.get("user-agent")),
    )


async def get_caller(
    request: Request,
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
    # A first request creates the person's row; that write is audited too.
    user = await users_crud.get_or_create_from_token(
        db, claims, audit=request_audit(request, ActorType.USER, None)
    )
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


async def get_platform_audit(request: Request, admin: PlatformAdmin) -> AuditContext:
    """Who did it, for a platform endpoint's audit event: always the Platform Admin."""
    return request_audit(request, ActorType.PLATFORM_ADMIN, admin)


PlatformAudit = Annotated[AuditContext, Depends(get_platform_audit)]


@dataclass(frozen=True)
class WorkspaceAccess:
    """The workspace a request acts on, and why the caller may act there."""

    workspace_id: uuid.UUID
    slug: str
    status: WorkspaceStatus
    user: User
    role: Role | None  # None: a Platform Admin who isn't a member
    # For the audit event: `platform_admin` when only platform power allowed it.
    audit: AuditContext


class PermissionCheck:
    """Steps 2 and 3 of every workspace request (PLAN.md section 6).

    One query resolves the slug in the URL and the caller's membership. An
    unknown workspace, or one the caller isn't a member of, answers 404, so
    it doesn't reveal that the workspace exists. A role without the
    permission answers 403 permission_denied. A Platform Admin may act in
    every workspace; where their own role doesn't cover it, that power
    reaches every tenant, so it needs MFA, as on the platform endpoints.

    A suspended workspace is read-only for its members: any write answers
    403 workspace_suspended (decision 10). Only platform power still writes
    there (with MFA, audited as such), e.g. to delete an abusive workspace.
    A deleted one is not found at all.

    A class (not a closure) so the guard tests can read `.permission` from
    every endpoint's dependencies.
    """

    def __init__(self, permission: Permission) -> None:
        self.permission = permission

    async def __call__(
        self, slug: str, request: Request, caller: CurrentCaller, db: DbSession
    ) -> WorkspaceAccess:
        found = await workspaces_crud.get_workspace_and_role(db, slug, user_id=caller.user.id)
        if found is None:
            raise NotFoundError(WORKSPACE_NOT_FOUND)
        workspace_id, status, role = found
        role_allowed = role is not None and role_allows(role, self.permission)
        read_only = status == WorkspaceStatus.SUSPENDED and not self.permission.is_read
        actor_type = ActorType.USER
        if role_allowed and not read_only:
            pass
        elif caller.user.is_platform_admin:
            actor_type = ActorType.PLATFORM_ADMIN
            if not caller.claims.has_mfa:
                raise ForbiddenError("mfa_required")
        elif role is None:
            raise NotFoundError(WORKSPACE_NOT_FOUND)
        elif not role_allowed:
            raise ForbiddenError("permission_denied")
        else:
            raise ForbiddenError("workspace_suspended")
        return WorkspaceAccess(
            workspace_id=workspace_id,
            slug=slug,
            status=status,
            user=caller.user,
            role=role,
            audit=request_audit(request, actor_type, caller.user),
        )


def require_permission(permission: Permission) -> Any:
    """`access: Annotated[WorkspaceAccess, require_permission(Permission.SERVICES_READ)]`."""
    return Depends(PermissionCheck(permission))


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
