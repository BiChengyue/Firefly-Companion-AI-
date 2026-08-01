from fastapi.testclient import TestClient


def test_sessions_crud(api_client: TestClient):
    # 创建（REST 契约：body 使用 id 字段）
    r = api_client.post("/api/sessions", json={"id": "sess-1", "title": "测试会话"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    # 列表
    r = api_client.get("/api/sessions")
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())

    # 重命名
    r = api_client.patch(f"/api/sessions/{sid}/rename", json={"title": "改名"})
    assert r.status_code == 200
    assert r.json()["title"] == "改名"

    # 删除
    r = api_client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200

    # 再次删除应 404（验证已删除）
    r = api_client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 404
