from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


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
