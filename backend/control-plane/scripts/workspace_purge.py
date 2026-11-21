"""The workspace purge job: deletes the data of workspaces deleted long enough ago.

    python -m scripts.workspace_purge          # now and every 24 hours (its container)
    python -m scripts.workspace_purge --once   # one round, e.g. by hand or from cron

Needs only PURGE_DATABASE_URL (the apim_purge login). When a workspace is
due is decided at deletion (its purge_after, from PURGE_AFTER_DAYS), so the
job needs no period of its own. Stops cleanly on SIGTERM.
"""

import asyncio
import logging
import os
import signal
import sys

from app.core.jobs import DAY_SECONDS
from app.workspaces.purge import purge_due, run


async def main(argv: list[str]) -> None:
    url = os.environ["PURGE_DATABASE_URL"]
    if "--once" in argv:
        purged = await purge_due(url)
        print(f"purged {len(purged)} workspaces: {', '.join(purged) or '-'}")
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run(url, every=DAY_SECONDS, stop=stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main(sys.argv[1:]))
