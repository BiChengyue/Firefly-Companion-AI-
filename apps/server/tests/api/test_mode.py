from fastapi.testclient import TestClient


def test_get_mode(api_client: TestClient):
    r = api_client.get("/api/mode")
    assert r.status_code == 200, r.text
    assert r.json()["current"] in ("daily", "work")


def test_switch_mode(api_client: TestClient):
    r = api_client.post("/api/mode", params={"mode": "work"})
    assert r.status_code == 200, r.text
    assert r.json()["current"] == "work"
