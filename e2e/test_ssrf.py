"""MT.9 stories: no tenant can make the gateway reach the platform's insides.

The stack allows only the mock backend (UPSTREAM_ALLOW=mock-backend), which
every other story uses. A service that names an internal host is refused by
the gateway when it connects; one that is plainly internal (an internal IP
literal, localhost) is refused by the control plane when it is saved.
"""

import httpx
import pytest

from conftest import World, wait_for


def test_a_service_pointing_at_redis_is_refused_by_the_gateway(
    gateway: httpx.Client, world: World
) -> None:
    # `redis` is just a name to the control plane; the gateway sees it resolve
    # to a Docker address (172.16.0.0/12) and refuses before connecting.
    service = world.service("redis", upstream_url="http://redis:6379")
    route = world.route(service, "redis", auth_required=False)

    r = wait_for(lambda: gateway.get(route["path_prefix"]), 502)

    assert r.status_code == 502
    assert r.json() == {"error": "upstream_not_allowed"}


@pytest.mark.parametrize(
    "upstream_url",
    ["http://127.0.0.1:9000", "http://169.254.169.254/latest", "http://localhost:5432"],
)
def test_a_plainly_internal_upstream_is_refused_on_save(world: World, upstream_url: str) -> None:
    r = world.admin.post(
        f"{world.base}/services",
        json={"name": world.name("internal"), "targets": [{"url": upstream_url}]},
    )

    assert r.status_code == 422
    assert "must not point inside the platform" in r.text
