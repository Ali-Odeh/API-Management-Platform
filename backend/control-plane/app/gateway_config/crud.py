"""Reading the config sequence and the gateway's config, workspace by workspace.

(Writing the sequence and the versions is the database triggers' job, see models.py.)
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, MutableMapping
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.consumers.models import Consumer
from app.gateway_config.models import SINGLETON_ID, ConfigSeq, WorkspaceConfig
from app.gateway_config.schemas import (
    ConfigAnswer,
    GatewayLimits,
    KeyConfig,
    RouteConfig,
    ServiceConfig,
    TargetConfig,
    WorkspaceConfigOut,
)
from app.keys.models import ApiKey
from app.routes.models import Route
from app.services.models import Service
from app.workspaces.limits import default_limit
from app.workspaces.models import Workspace, WorkspaceStatus

# Built workspaces, by id: one entry per workspace, for its latest version.
WorkspaceCache = MutableMapping[uuid.UUID, WorkspaceConfigOut]

_SEQ_QUERY = select(ConfigSeq.seq).where(ConfigSeq.id == SINGLETON_ID)
_IN_CONFIG = (WorkspaceStatus.ACTIVE, WorkspaceStatus.SUSPENDED)
# Until service targets exist (MT.10), a service's upstream_url is its one target.
_ONLY_TARGET_WEIGHT = 100


async def get_config_seq(db: AsyncSession) -> ConfigSeq:
    """The sequence row, always read fresh from the database.

    populate_existing: if this session already loaded the row, refresh it
    instead of returning the cached copy, since triggers change it behind
    the ORM's back.
    """
    query = (
        select(ConfigSeq)
        .where(ConfigSeq.id == SINGLETON_ID)
        .execution_options(populate_existing=True)
    )
    return (await db.scalars(query)).one()


async def read_seq(engine: AsyncEngine) -> int:
    """Just the number: a cheap query, run on every gateway request."""
    async with engine.connect() as conn:
        return (await conn.execute(_SEQ_QUERY)).scalar_one()


async def read_answer(
    engine: AsyncEngine,
    *,
    since: int,
    slugs: frozenset[str] | None,
    cache: WorkspaceCache,
) -> ConfigAnswer:
    """What changed after `since`, from ONE consistent view of the database.

    A full answer (every workspace) when `since` is 0 (a gateway starting),
    newer than the database's sequence (e.g. after a database reset), or
    otherwise unusable. Else only the workspaces whose seq is above `since`:
    those still served come back complete, the deleted and purged ones by id.
    `slugs` limits the answer to those workspaces (a dedicated gateway).

    REPEATABLE READ: every query sees the database as of the first one, so
    the sequence, each version and its data always match. Without it, a
    change committing in between could label old data with a new version,
    and the gateway would never ask for it again.

    `cache` holds each workspace built for its latest version: a workspace
    that didn't change since another gateway asked is not built again.
    """
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="REPEATABLE READ")
        async with conn.begin():
            seq = (await conn.execute(_SEQ_QUERY)).scalar_one()
            full = since <= 0 or since > seq
            query = select(
                Workspace.id, Workspace.slug, Workspace.status, WorkspaceConfig.version
            ).join(WorkspaceConfig, WorkspaceConfig.workspace_id == Workspace.id)
            if full:
                query = query.where(Workspace.status.in_(_IN_CONFIG))
            else:
                query = query.where(WorkspaceConfig.seq > since)
            if slugs is not None:
                query = query.where(Workspace.slug.in_(slugs))
            rows = (await conn.execute(query.order_by(Workspace.slug))).all()

            served = [row for row in rows if row.status in _IN_CONFIG]
            missing = [
                row.id
                for row in served
                if row.id not in cache or cache[row.id].version != row.version
            ]
            if missing:
                cache.update(await _build(conn, missing))
            deleted = sorted(row.id for row in rows if row.status not in _IN_CONFIG)
            for workspace_id in deleted:
                cache.pop(workspace_id, None)
            return ConfigAnswer(
                seq=seq,
                full=full,
                workspaces=[cache[row.id] for row in served],
                deleted=deleted,
            )


async def _build(
    conn: AsyncConnection, workspace_ids: list[uuid.UUID]
) -> dict[uuid.UUID, WorkspaceConfigOut]:
    """Each workspace's config, complete: four queries however many are built."""
    workspaces = await conn.execute(
        select(
            Workspace.id,
            Workspace.slug,
            Workspace.status,
            Workspace.rate_limit_per_min,
            Workspace.max_concurrent,
            WorkspaceConfig.version,
        )
        .join(WorkspaceConfig, WorkspaceConfig.workspace_id == Workspace.id)
        .where(Workspace.id.in_(workspace_ids))
    )
    services = _grouped(
        await conn.execute(
            select(Service.workspace_id, Service.id, Service.timeout_ms, Service.upstream_url)
            .where(Service.workspace_id.in_(workspace_ids), Service.is_active)
            .order_by(Service.id)
        )
    )
    routes = _grouped(
        await conn.execute(
            select(
                Route.workspace_id,
                Route.id,
                Route.service_id,
                Route.path_prefix,
                Route.methods,
                Route.strip_prefix,
                Route.auth_required,
            )
            .join(Service, Route.service_id == Service.id)
            .where(Route.workspace_id.in_(workspace_ids), Route.is_active, Service.is_active)
            .order_by(Route.path_prefix)
        )
    )
    keys = _grouped(
        await conn.execute(
            select(
                ApiKey.workspace_id,
                ApiKey.id,
                ApiKey.key_hash,
                ApiKey.consumer_id,
                Consumer.status.label("consumer_status"),
                Consumer.rate_limit_per_min,
            )
            .join(Consumer, ApiKey.consumer_id == Consumer.id)
            .where(ApiKey.workspace_id.in_(workspace_ids), ApiKey.revoked_at.is_(None))
            .order_by(ApiKey.id)
        )
    )
    built: dict[uuid.UUID, WorkspaceConfigOut] = {}
    for workspace in workspaces:
        built[workspace.id] = WorkspaceConfigOut(
            id=workspace.id,
            slug=workspace.slug,
            status=workspace.status,
            version=workspace.version,
            hosts=[workspace.slug],
            limits=GatewayLimits(
                rate_limit_per_min=_effective(workspace.rate_limit_per_min, "rate_limit_per_min"),
                max_concurrent=_effective(workspace.max_concurrent, "max_concurrent"),
            ),
            services=[
                ServiceConfig(
                    id=s.id,
                    timeout_ms=s.timeout_ms,
                    targets=[TargetConfig(id=s.id, url=s.upstream_url, weight=_ONLY_TARGET_WEIGHT)],
                )
                for s in services[workspace.id]
            ],
            routes=[RouteConfig.model_validate(r) for r in routes[workspace.id]],
            keys=[KeyConfig.model_validate(k) for k in keys[workspace.id]],
        )
    return built


def _grouped(rows: Iterable[Row[Any]]) -> defaultdict[uuid.UUID, list[Row[Any]]]:
    by_workspace: defaultdict[uuid.UUID, list[Row[Any]]] = defaultdict(list)
    for row in rows:
        by_workspace[row.workspace_id].append(row)
    return by_workspace


def _effective(own: int | None, column: str) -> int:
    return own if own is not None else default_limit(column)
