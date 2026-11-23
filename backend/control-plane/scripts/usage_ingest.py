"""The usage-ingest worker: the gateway's usage events into the request log (V2.1).

    python -m scripts.usage_ingest

Needs INGEST_DATABASE_URL (the apim_ingest login) and REDIS_URL; optionally
REQUEST_LOG_RETENTION_DAYS (default 90). Its name in the consumer group is
the host name (the container's id under Compose). Stops cleanly on SIGTERM.
"""

import asyncio
import logging
import os
import signal
import socket

from redis.asyncio import Redis

from app.usage.ingest import DEFAULT_RETENTION_DAYS, Ingester, open_pool


async def main() -> None:
    days = int(os.environ.get("REQUEST_LOG_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS)
    if days < 1:
        raise SystemExit("REQUEST_LOG_RETENTION_DAYS must be at least 1")
    pool = await open_pool(os.environ["INGEST_DATABASE_URL"])
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    ingester = Ingester(redis, pool, consumer=socket.gethostname(), retention_days=days)
    logging.getLogger("usage_ingest").info(
        "usage ingest started as %s, keeping %d days", ingester.consumer, days
    )
    try:
        await ingester.run(stop)
    finally:
        await redis.aclose()
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
