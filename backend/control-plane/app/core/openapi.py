"""Helpers for the OpenAPI docs (Swagger), so every router documents errors the same way."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

Responses = dict[int | str, dict[str, Any]]


class ErrorOut(BaseModel):
    """The plan's error format (section 6): {"detail": "..."}."""

    detail: str


def error_responses(errors: Mapping[int, str]) -> Responses:
    """{404: "Service not found"} -> the `responses=` value for a route decorator."""
    return {code: {"description": text, "model": ErrorOut} for code, text in errors.items()}
