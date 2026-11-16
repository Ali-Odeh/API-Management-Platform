"""Creates the FastAPI app. Each feature's router gets included here."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Code before `yield` runs at startup, code after it at shutdown.
    yield
    await engine.dispose()  # close all pooled DB connections cleanly


app = FastAPI(
    title="API Management Platform - Control Plane",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness: the process is up. Deliberately does not touch the DB."""
    return {"status": "ok"}
