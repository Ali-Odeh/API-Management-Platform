"""Shared pagination for every list endpoint (plan section 6).

Request:   GET /api/v1/services?page=2&page_size=20
Response:  {"items": [...], "total": 57}

Usage in a router:
    async def list_things(page: Pagination) -> Page[ThingOut]: ...
"""

from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

T = TypeVar("T")


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        """Rows to skip: page 1 -> 0, page 2 -> page_size, ..."""
        return (self.page - 1) * self.page_size


def _page_params(
    page: Annotated[int, Query(ge=1, description="Page number, starting at 1")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Items per page")
    ] = DEFAULT_PAGE_SIZE,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


Pagination = Annotated[PageParams, Depends(_page_params)]


class Page(BaseModel, Generic[T]):
    """`Page[ServiceOut]` -> {"items": [ServiceOut, ...], "total": int}"""

    items: list[T]
    total: int
