"""The request log: one row per request the gateway answered (V2.1).

The gateway writes a usage event per request to a Redis Stream; the
usage-ingest worker (ingest.py) inserts them here. The table is partitioned
by month on `ts`, so retention drops whole months instead of deleting rows,
and a query over a day reads only that month.

No foreign keys: a row outlives what it names (a deleted route, consumer or
key), and the ids come from the gateway, which may run on an older config.
The primary key includes `ts` because Postgres requires the partition key in
every unique index; `event_id` alone is unique in practice (a UUIDv7 made by
the gateway), and (`event_id`, `ts`) is what makes a re-read event insert
once.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, SmallInteger, String
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

REQUEST_ID_MAX_LENGTH = 128
PATH_MAX_LENGTH = 2048


class RequestLog(Base):
    __tablename__ = "request_logs"
    __table_args__ = (
        Index("ix_request_logs_workspace_ts", "workspace_id", "ts"),
        Index("ix_request_logs_workspace_consumer_ts", "workspace_id", "consumer_id", "ts"),
        {"postgresql_partition_by": "RANGE (ts)"},
    )

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID]
    request_id: Mapped[str | None] = mapped_column(String(REQUEST_ID_MAX_LENGTH))
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(PATH_MAX_LENGTH))
    route_id: Mapped[uuid.UUID | None]
    target_id: Mapped[uuid.UUID | None]
    consumer_id: Mapped[uuid.UUID | None]
    key_id: Mapped[uuid.UUID | None]
    status: Mapped[int] = mapped_column(SmallInteger)
    latency_ms: Mapped[float] = mapped_column(Float)
    upstream_ms: Mapped[float] = mapped_column(Float)
    bytes: Mapped[int] = mapped_column(BigInteger)
    client_ip: Mapped[str | None] = mapped_column(INET)
    ratelimit: Mapped[str | None] = mapped_column(String(20))
