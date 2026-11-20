"""Rebind a person to the GoTrue account that holds their email now (explicit recovery).

    docker compose exec control-plane python -m scripts.relink_user admin@example.com

A row stays bound to the GoTrue account it was first linked to; nothing
rebinds it automatically, because a matching email alone doesn't prove it is
the same person. When the binding no longer fits (e.g. GoTrue's database was
reset and the account made again; startup logs this as a conflict), an
operator runs this on purpose. The account is found through GoTrue's admin API.
"""

import asyncio
import sys


async def relink(email: str) -> int:
    # Imported here: the app needs settings from the environment.
    from app.core.config import get_settings
    from app.core.db import SessionLocal, engine
    from app.core.errors import AppError
    from app.gotrue.client import create_gotrue_admin
    from app.users import crud

    gotrue = create_gotrue_admin(get_settings())
    try:
        async with SessionLocal() as db:
            old, new = await crud.relink_user(db, gotrue, email=email)
    except AppError as exc:
        print(f"Not changed: {exc.detail}", file=sys.stderr)
        return 1
    finally:
        await gotrue.aclose()
        await engine.dispose()
    print(f"{email}: GoTrue account {old or 'none'} -> {new}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    return asyncio.run(relink(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
