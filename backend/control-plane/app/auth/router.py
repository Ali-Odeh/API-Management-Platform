from fastapi import APIRouter, HTTPException, status

from app.auth import crud
from app.auth.schemas import AdminOut, LoginRequest, TokenResponse
from app.core.config import get_settings
from app.core.deps import CurrentAdmin, DbSession
from app.core.security import create_access_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    """Email + password in, access token out."""
    admin = await crud.authenticate(db, body.email, body.password)
    if admin is None:
        # Same message for unknown email and wrong password, on purpose.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    minutes = get_settings().jwt_expire_minutes
    return TokenResponse(
        access_token=create_access_token(str(admin.id)),
        expires_in=minutes * 60,
    )


@router.get("/me", response_model=AdminOut)
async def me(admin: CurrentAdmin) -> AdminOut:
    """The admin who owns the token."""
    return AdminOut.model_validate(admin)
