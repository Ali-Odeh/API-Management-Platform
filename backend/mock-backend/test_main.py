import time

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "mock-backend"}


def test_echo_get_returns_method_path_and_query():
    r = client.get("/v1/orders/7?expand=items", headers={"X-Request-ID": "abc"})
    assert r.status_code == 200
    data = r.json()
    assert data["method"] == "GET"
    assert data["path"] == "/v1/orders/7"
    assert data["query"] == {"expand": "items"}
    assert data["headers"]["x-request-id"] == "abc"


def test_echo_post_returns_body():
    r = client.post("/v1/orders", content='{"qty": 2}')
    assert r.status_code == 200
    data = r.json()
    assert data["method"] == "POST"
    assert data["body"] == '{"qty": 2}'


def test_delay_ms_answers_late():
    start = time.monotonic()
    r = client.get("/v1/slow?delay_ms=200")
    assert r.status_code == 200
    assert time.monotonic() - start >= 0.2
    assert r.json()["query"] == {"delay_ms": "200"}


@pytest.mark.parametrize("delay", ["-1", "30001", "soon"])
def test_bad_delay_ms_is_422(delay):
    assert client.get(f"/v1/slow?delay_ms={delay}").status_code == 422


def test_the_echo_names_the_instance():
    assert client.get("/x").json()["service"] == "mock-backend"


def test_an_instance_can_act_down_and_come_back():
    try:
        assert client.put("/_mock/state", json={"up": False}).json() == {"up": False}
        r = client.get("/health")
        assert (r.status_code, r.json()["status"]) == (503, "down")
        r = client.get("/v1/orders")
        assert (r.status_code, r.json()) == (503, {"error": "mock_down", "service": "mock-backend"})
    finally:
        client.put("/_mock/state", json={"up": True})
    assert client.get("/health").status_code == 200
    assert client.get("/v1/orders").status_code == 200
