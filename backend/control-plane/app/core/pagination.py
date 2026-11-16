"""Shared pagination for every list endpoint (plan section 6).

Request:   GET /api/v1/services?page=2&page_size=20
Response:  {"items": [...], "total": 57}

Usage:
    router:  async def list_things(page: Pagination) -> Page[ThingOut]: ...
    crud:    items, total = await paginate(db, query, page)
"""

from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def paginate(
    db: AsyncSession, query: Select[tuple[T]], page: PageParams
) -> tuple[list[T], int]:
    """Run `query` for one page, plus a count of all matching rows.

    The caller decides the filters and the order; the count ignores the order
    (it doesn't change how many rows match, and Postgres can skip sorting).
    """
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = await db.scalar(count_query)
    rows = await db.scalars(query.offset(page.offset).limit(page.page_size))
    return list(rows), total or 0
