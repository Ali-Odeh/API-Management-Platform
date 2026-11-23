"""Reading the config sequence and the gateway's config, workspace by workspace.

(Writing the sequence and the versions is the database triggers' job, see models.py.)
"""

import json
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from sqlalchemy import Row, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.consumers.models import Consumer
from app.core.config import Settings, get_settings
from app.gateway_config.models import SINGLETON_ID, ConfigSeq, WorkspaceConfig
from app.gateway_config.schemas import (
    ConfigAnswer,
    CorsConfig,
    GatewayLimits,
    HealthConfig,
    KeyConfig,
    RouteConfig,
    ServiceConfig,
    TargetConfig,
    WorkspaceConfigOut,
)
from app.keys.models import ApiKey
from app.plans.models import Plan
from app.routes.models import Route
from app.services.models import Service, ServiceTarget
from app.workspaces.limits import default_limit
from app.workspaces.models import Workspace, WorkspaceStatus


class ConfigCursor(NamedTuple):
    generation: uuid.UUID
    seq: int


@dataclass
class WorkspaceCache:
    """One latest version per workspace, all from the same database history."""

    cursor: ConfigCursor | None = None
    workspaces: dict[uuid.UUID, WorkspaceConfigOut] = field(default_factory=dict)

    def advance(self, cursor: ConfigCursor) -> None:
        previous = self.cursor
        if previous and (cursor.generation != previous.generation or cursor.seq < previous.seq):
            self.workspaces.clear()
        self.cursor = cursor


_CURSOR_QUERY = select(ConfigSeq.generation, ConfigSeq.seq).where(ConfigSeq.id == SINGLETON_ID)
_IN_CONFIG = (WorkspaceStatus.ACTIVE, WorkspaceStatus.SUSPENDED)


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


async def read_cursor(engine: AsyncEngine) -> ConfigCursor:
    """A cheap query, run on every gateway request."""
    async with engine.connect() as conn:
        return ConfigCursor(*(await conn.execute(_CURSOR_QUERY)).one())


def config_defaults(settings: Settings) -> dict[str, int]:
    """The platform defaults that end up in config answers (see _build)."""
    return {
        "rate_limit_per_min": settings.workspace_rate_limit_per_min,
        "max_concurrent": settings.workspace_max_concurrent,
        "max_body_bytes": int(settings.max_request_body),
        "ip_rate_limit_per_min": settings.public_rate_limit_per_ip_per_min,
        "auth_fail_limit": settings.auth_fail_limit_per_ip,
    }


async def apply_config_defaults(engine: AsyncEngine, settings: Settings) -> None:
    """Bump the workspaces that inherit a default which changed since the
    last start, so running gateways get them on their next poll.

    The defaults come from the environment and change no row, so no trigger
    sees them; without this a gateway asking `since=N` would keep the old
    values until something else in that workspace changed, while a newly
    started gateway gets the new ones. The database function (migration
    "config defaults") compares, bumps and stores in one transaction.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT apply_config_defaults(CAST(:defaults AS jsonb))"),
            {"defaults": json.dumps(config_defaults(settings))},
        )


async def read_answer(
    engine: AsyncEngine,
    *,
    since: int,
    slugs: frozenset[str] | None,
    cache: WorkspaceCache,
    generation: uuid.UUID | None = None,
) -> tuple[ConfigCursor, ConfigAnswer]:
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
            cursor = ConfigCursor(*(await conn.execute(_CURSOR_QUERY)).one())
            cache.advance(cursor)
            full = (
                since <= 0
                or since > cursor.seq
                or (generation is not None and generation != cursor.generation)
            )
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
                if row.id not in cache.workspaces or cache.workspaces[row.id].version != row.version
            ]
            if missing:
                cache.workspaces.update(await _build(conn, missing))
            deleted = sorted(row.id for row in rows if row.status not in _IN_CONFIG)
            for workspace_id in deleted:
                cache.workspaces.pop(workspace_id, None)
            return cursor, ConfigAnswer(
                seq=cursor.seq,
                full=full,
                workspaces=[cache.workspaces[row.id] for row in served],
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
            Workspace.ip_rate_limit_per_min,
            WorkspaceConfig.version,
        )
        .join(WorkspaceConfig, WorkspaceConfig.workspace_id == Workspace.id)
        .where(Workspace.id.in_(workspace_ids))
    )
    targets: defaultdict[uuid.UUID, list[TargetConfig]] = defaultdict(list)
    for t in await conn.execute(
        select(ServiceTarget.service_id, ServiceTarget.id, ServiceTarget.url, ServiceTarget.weight)
        .where(ServiceTarget.workspace_id.in_(workspace_ids), ServiceTarget.is_active)
        .order_by(ServiceTarget.created_at, ServiceTarget.id)
    ):
        targets[t.service_id].append(TargetConfig(id=t.id, url=t.url, weight=t.weight))
    # A service is routable only with at least one active target.
    routable = select(ServiceTarget.service_id).where(ServiceTarget.is_active)
    services = _grouped(
        await conn.execute(
            select(
                Service.workspace_id,
                Service.id,
                Service.timeout_ms,
                Service.lb_algorithm,
                Service.hc_path,
                Service.hc_interval_s,
                Service.hc_unhealthy_threshold,
                Service.hc_healthy_threshold,
                Service.passive_failures,
                Service.passive_eject_s,
            )
            .where(
                Service.workspace_id.in_(workspace_ids),
                Service.is_active,
                Service.id.in_(routable),
            )
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
                Route.max_body_bytes,
                Route.cors_origins,
                Route.cors_methods,
                Route.cors_headers,
                Route.cors_credentials,
                Route.cors_max_age,
            )
            .join(Service, Route.service_id == Service.id)
            .where(
                Route.workspace_id.in_(workspace_ids),
                Route.is_active,
                Service.is_active,
                Service.id.in_(routable),
            )
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
                Consumer.plan_id,
                Plan.rate_limit_per_min,
                Plan.monthly_quota,
            )
            .join(Consumer, ApiKey.consumer_id == Consumer.id)
            .join(Plan, Consumer.plan_id == Plan.id)
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
                ip_rate_limit_per_min=_effective(
                    workspace.ip_rate_limit_per_min, "ip_rate_limit_per_min"
                ),
                auth_fail_limit=get_settings().auth_fail_limit_per_ip,
            ),
            services=[
                ServiceConfig(
                    id=s.id,
                    timeout_ms=s.timeout_ms,
                    lb_algorithm=s.lb_algorithm,
                    health=HealthConfig(
                        path=s.hc_path,
                        interval_s=s.hc_interval_s,
                        unhealthy=s.hc_unhealthy_threshold,
                        healthy=s.hc_healthy_threshold,
                        passive_failures=s.passive_failures,
                        passive_eject_s=s.passive_eject_s,
                    ),
                    targets=targets[s.id],
                )
                for s in services[workspace.id]
            ],
            routes=[_route_config(r) for r in routes[workspace.id]],
            keys=[KeyConfig.model_validate(k) for k in keys[workspace.id]],
        )
    return built


def _grouped(rows: Iterable[Row[Any]]) -> defaultdict[uuid.UUID, list[Row[Any]]]:
    by_workspace: defaultdict[uuid.UUID, list[Row[Any]]] = defaultdict(list)
    for row in rows:
        by_workspace[row.workspace_id].append(row)
    return by_workspace


def _route_config(r: Row[Any]) -> RouteConfig:
    return RouteConfig(
        id=r.id,
        service_id=r.service_id,
        path_prefix=r.path_prefix,
        methods=r.methods,
        strip_prefix=r.strip_prefix,
        auth_required=r.auth_required,
        max_body_bytes=(
            r.max_body_bytes if r.max_body_bytes is not None else get_settings().max_request_body
        ),
        cors=CorsConfig(
            origins=r.cors_origins,
            methods=r.cors_methods or r.methods,
            headers=r.cors_headers,
            credentials=r.cors_credentials,
            max_age=r.cors_max_age,
        ),
    )


def _effective(own: int | None, column: str) -> int:
    return own if own is not None else default_limit(column)
