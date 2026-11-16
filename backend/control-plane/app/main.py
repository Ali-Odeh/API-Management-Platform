"""Creates the FastAPI app. Each feature's router gets included here."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth import crud as auth_crud
from app.auth.router import router as auth_router
from app.consumers.router import router as consumers_router
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.core.errors import register_error_handlers
from app.routes.router import router as routes_router
from app.services.router import router as services_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: make sure the default workspace and the first admin exist.
    # (Tables already exist: the container runs `alembic upgrade head` first.)
    settings = get_settings()
    async with SessionLocal() as db:
        await auth_crud.seed_admin(db, email=settings.admin_email, password=settings.admin_password)
    yield
    # Shutdown: close all pooled DB connections cleanly.
    await engine.dispose()


app = FastAPI(
    title="API Management Platform - Control Plane",
    version="0.1.0",
    lifespan=lifespan,
)

register_error_handlers(app)

app.include_router(auth_router)
app.include_router(services_router)
app.include_router(routes_router)
app.include_router(consumers_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness: the process is up. Deliberately does not touch the DB."""
    return {"status": "ok"}
