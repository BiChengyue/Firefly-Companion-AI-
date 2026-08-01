from fastapi.testclient import TestClient


def test_health_ok(api_client: TestClient):
    r = api_client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "firefly-companion"
    # test profile 下只断言字段存在，不要求真实服务就绪
    assert "llmReady" in body
    assert "provider" in body
    assert "model" in body
    assert "version" in body
