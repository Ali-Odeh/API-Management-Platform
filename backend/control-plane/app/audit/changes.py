"""Building an event's `changes`: {"field": [before, after]}, changed fields only.

Values are made JSON-friendly (ids and times as strings). Callers pick the
fields, so a secret (a key's hash, a raw key) can never slip in.
"""

import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum
from typing import Any

Changes = dict[str, list[Any]]


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def snapshot(obj: object, fields: Iterable[str]) -> dict[str, Any]:
    """The named fields of a row, ready to compare and to store."""
    return {field: _jsonable(getattr(obj, field)) for field in fields}


def created(after: Mapping[str, Any]) -> Changes:
    return {field: [None, value] for field, value in after.items()}


def deleted(before: Mapping[str, Any]) -> Changes:
    return {field: [value, None] for field, value in before.items()}


def updated(before: Mapping[str, Any], after: Mapping[str, Any]) -> Changes:
    return {
        field: [before[field], after[field]] for field in after if before[field] != after[field]
    }
