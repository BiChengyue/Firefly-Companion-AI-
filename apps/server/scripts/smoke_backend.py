"""后端冒烟脚本：进程内起 FastAPI（TestClient），不占用 8765 端口，不依赖桌面端。

用途：AI 助手改完代码后快速验证「后端还能不能正常起来并响应核心接口」。

安全约定（与 tests/api/conftest.py 一致，绝不副作用你的项目）：
- FIREFLY_ENV=test 叠加 config/test.json（占位 Key / 关主动聊天 / 关 TTS）
- FIREFLY_DB_PATH 指向临时库，绝不触碰项目真实 data/app.db
- 屏蔽 lifespan 里的真实副作用：音频缓存清理、MCP 后台连接

用法：
    python scripts/smoke_backend.py
退出码 0=全部通过，非 0=有失败。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 让 `import main` / `import app` 可解析（apps/server 在 sys.path）
ROOT = Path(__file__).resolve().parents[1]  # apps/server
sys.path.insert(0, str(ROOT))

os.environ.setdefault("FIREFLY_ENV", "test")
_tmp = tempfile.mkdtemp(prefix="firefly_smoke_")
os.environ["FIREFLY_DB_PATH"] = os.path.join(_tmp, "smoke.db")

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402


def _patch_side_effects() -> None:
    """屏蔽 lifespan 的真实副作用（与测试 conftest 保持一致）。"""
    import app.core.tools.mcp_client as _mcp
    import app.core.voice.tts as _tts

    async def _noop_async():
        return None

    _mcp.start_all_enabled = _noop_async  # type: ignore[assignment]
    _mcp.shutdown_all = _noop_async  # type: ignore[assignment]
    _tts.cleanup_audio_cache = lambda: {  # type: ignore[assignment]
        "deleted_count": 0,
        "freed_mb": 0.0,
        "remaining_mb": 0.0,
    }


def _probe(client: TestClient, method: str, path: str, **kw) -> tuple[bool, str]:
    try:
        r = getattr(client, method)(path, **kw)
        ok = r.status_code == 200
        return ok, f"{method.upper()} {path} -> {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{method.upper()} {path} -> EXC {e}"


def main() -> int:
    _patch_side_effects()

    probes = [
        ("get", "/health"),
        ("get", "/api/mode"),
        ("get", "/api/workspaces"),
        ("get", "/api/system/status"),
        ("get", "/api/tools"),
        ("post", "/api/sessions", {"json": {"id": "smoke-1", "title": "smoke"}}),
        ("get", "/api/sessions"),
        ("delete", "/api/sessions/smoke-1"),
    ]

    results: list[tuple[bool, str]] = []
    with TestClient(app) as client:
        for item in probes:
            method, path = item[0], item[1]
            kw = item[2] if len(item) > 2 else {}
            ok, msg = _probe(client, method, path, **kw)
            results.append((ok, msg))
            print(("PASS" if ok else "FAIL") + " " + msg)

    failed = [m for ok, m in results if not ok]
    if failed:
        print(f"\n{len(failed)}/{len(results)} 探针失败")
        return 1
    print(f"\n{len(results)}/{len(results)} 探针全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
