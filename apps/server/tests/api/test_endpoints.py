from fastapi.testclient import TestClient

# 一组只读端点：验证路由到配置/DB 整条链路可达（不触发 LLM/外部网络）
READONLY = [
    "/api/mode",
    "/api/workspaces",
    "/api/concern/queue",
    "/api/concern/stats",
    "/api/system/status",
    "/api/memories",
    "/api/tools",
]


def test_readonly_endpoints_200(api_client: TestClient):
    for path in READONLY:
        r = api_client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"
