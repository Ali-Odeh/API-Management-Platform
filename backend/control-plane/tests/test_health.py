from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_openapi_docs_available(client: AsyncClient) -> None:
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "API Management Platform - Control Plane"
