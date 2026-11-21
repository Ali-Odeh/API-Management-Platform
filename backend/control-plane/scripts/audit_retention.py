"""The audit retention job: deletes audit events older than AUDIT_RETENTION_DAYS.

    python -m scripts.audit_retention          # now and every 24 hours (its container)
    python -m scripts.audit_retention --once   # one round, e.g. by hand or from cron

Needs only AUDIT_RETENTION_DATABASE_URL (the apim_retention login) and,
optionally, AUDIT_RETENTION_DAYS (default 365). Stops cleanly on SIGTERM.
"""

import asyncio
import logging
import os
import signal
import sys

from app.audit.retention import DEFAULT_DAYS, delete_expired, run
from app.core.jobs import DAY_SECONDS


async def main(argv: list[str]) -> None:
    url = os.environ["AUDIT_RETENTION_DATABASE_URL"]
    days = int(os.environ.get("AUDIT_RETENTION_DAYS") or DEFAULT_DAYS)
    if "--once" in argv:
        deleted = await delete_expired(url, days=days)
        print(f"deleted {deleted} audit events older than {days} days")
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run(url, days=days, every=DAY_SECONDS, stop=stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main(sys.argv[1:]))
