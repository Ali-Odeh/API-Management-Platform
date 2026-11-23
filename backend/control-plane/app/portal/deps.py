"""Who is calling the portal API: a developer, signed in with the developers' GoTrue.

    @router.get("/me")
    async def me(caller: CurrentDeveloper): ...

Only a token of `auth-portal` opens the portal API, and it opens nothing
else: a member's token, the Platform Admin's or a service-role key answer
401 here, exactly as a developer's token does on the admin API.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.audit.context import AuditContext
from app.audit.models import ActorType
from app.core.deps import DbSession
from app.core.errors import ForbiddenError
from app.core.identity import verify_developer_token
from app.developers import crud as developers_crud
from app.developers.models import Developer, DeveloperStatus

# Its own scheme, so Swagger shows the two kinds of token apart.
developer_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="DeveloperBearer",
    description="An access token of the developers' GoTrue (auth-portal)",
)


@dataclass(frozen=True)
class DeveloperCaller:
    developer: Developer
    # For their changes (P.2): recorded in the workspace's log as `developer`.
    audit: AuditContext


def _audit(request: Request, developer: Developer | None) -> AuditContext:
    return AuditContext(
        actor_type=ActorType.DEVELOPER,
        actor_user_id=developer.id if developer is not None else None,
        actor_email=developer.email if developer is not None else None,
        request_id=getattr(request.state, "request_id", None),
        ip=request.client.host if request.client else None,
        user_agent=AuditContext.clip_user_agent(request.headers.get("user-agent")),
    )


async def get_developer(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(developer_bearer)],
    db: DbSession,
) -> DeveloperCaller:
    """Verify the portal token and find the developer (made on their first request).

    `status` is read from the database on every request, so a block takes
    effect on the developer's very next call.
    """
    claims = verify_developer_token(credentials.credentials) if credentials else None
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    developer = await developers_crud.get_or_create_from_token(
        db, claims, audit=_audit(request, None)
    )
    if developer.status == DeveloperStatus.BLOCKED:
        raise ForbiddenError("developer_blocked")
    return DeveloperCaller(developer=developer, audit=_audit(request, developer))


CurrentDeveloper = Annotated[DeveloperCaller, Depends(get_developer)]
