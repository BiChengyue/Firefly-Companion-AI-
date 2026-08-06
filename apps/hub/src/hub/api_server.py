"""只读 HTTP 接口（P1：控制中心只读网关）。

- 绑定 127.0.0.1（同机 qbot 调用；不对外暴露）
- 令牌鉴权：?token=<PCH_TOKEN>（或环境变量 PCH_TOKEN；与 reasonix-serve 的 URL token 风格一致）
- 端点：
  GET /api/v1/health           未认证 → 401
  GET /api/v1/context          脱敏上下文（ContextGateway）
  GET /api/v1/server-status    服务器 CPU/内存/磁盘/服务状态（psutil + sc query）
- 全部只读；无写操作、无任意执行。
"""
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.hub.context_gateway import ContextGateway
from src.hub.ingress import DeviceRegistry
from src.hub.projects import list_projects
from src.hub.state_engine import StateEngine
from src.hub.storage import HubStore

# 电脑检测器类别白名单（拒伪造/未知类别注入）
COMPUTER_CATEGORIES = {
    "game", "star_rail", "work", "coding", "design", "video", "browsing",
    "communication", "idle", "away", "offline", "unknown", "tool",
}
_MAX_CONTENT = 200  # game/video 内容名长度上限


def ingest_computer(store: HubStore, body: dict) -> str | None:
    """校验并写入电脑状态。返回错误消息或 None。"""
    if not isinstance(body, dict):
        return "payload must be object"
    category = str(body.get("category", "")).strip()
    if category not in COMPUTER_CATEGORIES:
        return f"category not allowed: {category!r}"
    at = body.get("at")
    try:
        at = float(at)
    except (TypeError, ValueError):
        return "at must be number"
    game = body.get("game")
    video = body.get("video")
    if game is not None and (not isinstance(game, str) or len(game) > _MAX_CONTENT):
        return "game too long or invalid"
    if video is not None and (not isinstance(video, str) or len(video) > _MAX_CONTENT):
        return "video too long or invalid"
    store.put_computer_state(
        category=category,
        at=at,
        raw=body,
        game=game,
        video=video,
        focus_monitor=body.get("focus_monitor"),
        nearby=body.get("nearby"),
        idle_seconds=body.get("idle_seconds"),
    )
    store.audit("computer-sensor", "ingest", "computer", "ok", category)
    return None

DATA_DIR = os.environ.get("PCH_DATA_DIR", str(Path.home() / "pch-data"))
WATCHED_SERVICES = os.environ.get("PCH_WATCHED_SERVICES", "firefly-qbot").split(",")

# ── D-4 精确位置授权（CONTRACTS §5 / WORKFLOW_REVIEW C1/C2）──
# 默认允许、不加模糊；限频 1 次/分钟、hub_audit 审计、仅内网/Tailnet 访问（不进 frp）。
PHONE_LOCATION_INTERVAL = 60.0  # 秒
_phone_loc_rates: dict[str, float] = {}  # client_ip → last_ok_ts
_phone_loc_lock = threading.Lock()


def _is_private_client(ip: str) -> bool:
    """内网/Tailnet 判定：本机回环、RFC1918 私有、Tailscale CGNAT 100.64.0.0/10。

    警示（WORKFLOW_REVIEW C2）：基于 source IP 判定，若 hub-api 经 frp 反代暴露
    （出口为 127.0.0.1）则公网请求会被误放行——部署上必须保证本端点不进 frp。
    """
    if ip in ("127.0.0.1", "::1"):
        return True
    try:
        import ipaddress

        a = ipaddress.ip_address(ip)
        if a.version == 4:
            n = int(a)
            start = (100 << 24) | (64 << 16)   # 100.64.0.0
            end = start + (1 << 22)            # 100.128.0.0
            if start <= n < end:               # 100.64.0.0/10 Tailnet CGNAT
                return True
        return bool(a.is_private or a.is_loopback or a.is_reserved)
    except ValueError:
        return False


def _token() -> str:
    """动态解析令牌（环境变量优先，其次令牌文件）；不要在 import 时缓存。"""
    return os.environ.get("PCH_TOKEN", "") or _read_token_file()


MAX_BODY = 64 * 1024  # POST body 上限 64KB（防内存 DoS）


_FITNESS_CACHE = "C:/ProgramData/firefly-bot/data/fitness_latest.json"


def _read_fitness_cache() -> dict | None:
    try:
        with open(_FITNESS_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_fitness_cache(body: dict):
    import os as _os
    _os.makedirs(_os.path.dirname(_FITNESS_CACHE), exist_ok=True)
    with open(_FITNESS_CACHE, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)


def _read_body(handler) -> str | None:
    """读取并校验 body；超限返回 None（调用方回 413）。"""
    length = int(handler.headers.get("Content-Length", "0"))
    if length > MAX_BODY:
        return None
    return handler.rfile.read(length).decode("utf-8")


def _auth_ok(handler) -> bool:
    """Header token 鉴权（优先）或 URL query token（兼容过渡）。恒定时间比较。"""
    tok = handler.headers.get("X-PCH-Token", "")
    if not tok:
        query = parse_qs(urlparse(handler.path).query)
        tok = query.get("token", [""])[0]
    return hmac.compare_digest(tok, _token())


class HubHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self):
        self._json(404, {"error": {"code": "NOT_FOUND", "message": "unknown endpoint"}})

    def _phone_location(self):
        """GET /api/v1/phone-location：精确位置授权（D-4，CONTRACTS §5）。

        鉴权（X-PCH-Token，_auth_ok 已过）→ 内网/Tailnet 限定（C2）→ 限频 1 次/分钟（C1）
        → hub_audit 审计落库 → 返回精确坐标（默认允许、不加模糊，开关可关闭）。
        """
        if os.environ.get("PCH_PHONE_LOCATION_ENABLED", "1") != "1":
            self._json(403, {"error": {"code": "DISABLED", "message": "phone-location disabled"}})
            return
        client = self.client_address[0]
        if not _is_private_client(client):
            self.server.store.audit("companion", "phone-location", "phone", "denied", f"non-private client {client}")
            self._json(403, {"error": {"code": "FORBIDDEN", "message": "phone-location only on private/Tailnet"}})
            return
        now = time.time()
        with _phone_loc_lock:
            last = _phone_loc_rates.get(client, 0.0)
            if now - last < PHONE_LOCATION_INTERVAL:
                self._json(429, {"error": {"code": "RATE_LIMITED", "message": "phone-location 1/min"}})
                return
            _phone_loc_rates[client] = now
        latest = self.server.store.get_phone_state(1)
        if not latest:
            self.server.store.audit("companion", "phone-location", "phone", "not-found", client)
            self._json(404, {"error": {"code": "UNKNOWN_STATE", "message": "no phone state yet"}})
            return
        loc = str(latest[0].get("loc_bucket") or "unknown")[:64]
        self.server.store.audit("companion", "phone-location", "phone", "ok", client)
        self._json(200, {"loc": loc, "at": latest[0].get("at")})

    def do_GET(self):
        parsed = urlparse(self.path)
        if not _auth_ok(self):
            self._json(401, {"error": {"code": "UNAUTHORIZED", "message": "missing or bad token"}})
            return
        path = parsed.path
        if path == "/api/v1/health":
            self._json(200, {"status": "ok"})
        elif path == "/api/v1/events":
            events = self.server.store.get_unconsumed_events()
            self._json(200, {"events": events})
        elif path == "/api/v1/context":
            ctx = self.server.gateway.build(f"api-{threading.get_ident()}")
            if ctx is None:
                self._json(503, {"error": {"code": "UNKNOWN_STATE", "message": "no usable context"}})
            else:
                self._json(200, ctx)
        elif path == "/api/v1/server-status":
            self._json(200, server_status())
        elif path == "/api/v1/projects":
            self._json(200, {"projects": list_projects()})
        elif path == "/api/v1/computer-state":
            latest = self.server.store.get_computer_state(1)
            if not latest:
                self._json(404, {"error": {"code": "UNKNOWN_STATE", "message": "no computer state yet"}})
            else:
                d = latest[0]
                # stale 标记：以服务器收到时间（received_at）为准，避免设备时钟偏差
                base = float(d.get("received_at") or d.get("at", 0))
                age = time.time() - base
                d["stale"] = age > 600
                d["age_seconds"] = round(age)
                self._json(200, d)
        elif path == "/api/v1/sr-account":
            from src.hub.sr_account import get_sr_account

            self._json(200, get_sr_account())
        elif path == "/api/v1/fitness-state":
            d = _read_fitness_cache()
            if not d:
                self._json(404, {"error": {"code": "UNKNOWN_STATE", "message": "no fitness data yet"}})
            else:
                age = round(time.time() - float(d.get("received_at", 0)))
                d["age_seconds"] = age
                d["fresh"] = age < 900
                self._json(200, d)
        elif path == "/api/v1/phone-state":
            latest = self.server.store.get_phone_state(1)
            if not latest:
                self._json(404, {"error": {"code": "UNKNOWN_STATE", "message": "no phone state yet"}})
            else:
                d = latest[0]
                # 脱敏策略：loc 为精确坐标（数据中心内部），loc_bucket 为粗粒度区域（对外）
                raw_loc = str(d.get("loc_bucket") or "")
                if "," in raw_loc:
                    try:
                        lat, lon = raw_loc.split(",")
                        d["loc"] = raw_loc
                        d["loc_bucket"] = f"{float(lat):.2f},{float(lon):.2f}"
                    except Exception:
                        d["loc_bucket"] = "unknown"
                # raw 原始载荷里也可能带精确坐标——对外一律剥掉
                if isinstance(d.get("raw"), dict):
                    raw_clean = {k: v for k, v in d["raw"].items() if k != "loc"}
                    d["raw"] = raw_clean
                    # 采集增强字段透出顶层
                    for k in ("charging", "network", "dnd"):
                        if k in raw_clean:
                            d[k] = raw_clean[k]
                self._json(200, d)
        elif path == "/api/v1/phone-location":
            self._phone_location()
        else:
            self._not_found()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/events/consumed":
            # 流萤消费确认：X-Phone-Token 头鉴权
            if self.headers.get("X-Phone-Token", "") != os.environ.get("PCH_PHONE_TOKEN", ""):
                self._json(401, {"error": {"code": "UNAUTHORIZED", "message": "bad phone token"}})
                return
            try:
                raw = _read_body(self)
                if raw is None:
                    self._json(413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "body > 64KB"}})
                    return
                body = json.loads(raw)
                self.server.store.mark_event_consumed(int(body.get("id", 0)))
                self._json(200, {"status": "ok"})
            except Exception as e:
                self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": str(e)}})
            return
        if parsed.path == "/api/v1/ingest/event":
            # 检测器事件上报（pushd）：X-Phone-Token 头鉴权
            if self.headers.get("X-Phone-Token", "") != os.environ.get("PCH_PHONE_TOKEN", ""):
                self._json(401, {"error": {"code": "UNAUTHORIZED", "message": "bad phone token"}})
                return
            try:
                raw = _read_body(self)
                if raw is None:
                    self._json(413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "body > 64KB"}})
                    return
                body = json.loads(raw)
                kind = str(body.get("kind", ""))[:32]
                data = body.get("data", {})
                if not kind:
                    raise ValueError("kind required")
                self.server.store.put_push_event(kind, data)
                self._json(200, {"status": "ok"})
            except Exception as e:
                self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": str(e)}})
            return
        if parsed.path == "/api/v1/ingest/phone":
            # 手机采集器上报：X-Phone-Token 头鉴权
            expected_token = os.environ.get("PCH_PHONE_TOKEN", "")
            if self.headers.get("X-Phone-Token", "") != expected_token:
                self._json(401, {"error": {"code": "UNAUTHORIZED", "message": "bad phone token"}})
                return
            try:
                raw = _read_body(self)
                if raw is None:
                    self._json(413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "body > 64KB"}})
                    return
                body = json.loads(raw)
            except Exception:
                self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": "bad json"}})
                return
            try:
                at = float(body.get("at", time.time()))
                # 手机上传精确坐标（loc）；旧版字段 loc_bucket 兼容
                loc = str(body.get("loc") or body.get("loc_bucket") or "unknown")[:64]
                screen = body.get("screen")
                battery = int(body.get("battery", -1)) if body.get("battery") is not None else None
                self.server.store.put_phone_state(at, loc, screen, battery, body)
                self._json(200, {"status": "ok"})
            except Exception as e:
                self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": str(e)}})
            return
        if not _auth_ok(self):
            self._json(401, {"error": {"code": "UNAUTHORIZED", "message": "missing or bad token"}})
            return
        if parsed.path == "/api/v1/ingest/fitness":
            if not _auth_ok(self):
                self._json(401, {"error": {"code": "UNAUTHORIZED", "message": "bad token"}})
                return
            try:
                raw = _read_body(self)
                if raw is None:
                    self._json(413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "body > 64KB"}})
                    return
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError("body must be object")
                body["received_at"] = time.time()
                _write_fitness_cache(body)
                self._json(200, {"status": "ok"})
            except Exception as e:
                self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": str(e)}})
            return
        if parsed.path != "/api/v1/ingest/computer":
            self._not_found()
            return
        try:
            raw = _read_body(self)
            if raw is None:
                self._json(413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "body > 64KB"}})
                return
            body = json.loads(raw)
        except Exception:
            self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": "bad json"}})
            return
        err = ingest_computer(self.server.store, body)
        if err:
            self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": err}})
        else:
            self._json(200, {"status": "ok"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[hub-api] " + (fmt % args) + "\n")


def server_status() -> dict:
    """只读服务器状态（CPU/内存/磁盘/白名单服务状态）。"""
    import psutil

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
    services = {}
    if os.name == "nt":
        for name in WATCHED_SERVICES:
            name = name.strip()
            if not name:
                continue
            try:
                out = subprocess.run(
                    ["sc", "query", name], capture_output=True, text=True, timeout=5
                ).stdout
                services[name] = "running" if "RUNNING" in out else "not-running"
            except Exception:
                services[name] = "unknown"
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / 1024**3, 2),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / 1024**3, 2),
        "services": services,
        "as_of": __import__("datetime").datetime.now().isoformat(),
    }


def make_server(store: HubStore, engine: StateEngine, registry: DeviceRegistry, port: int = 8901):
    gw = ContextGateway(store, engine)
    bind = os.environ.get("PCH_BIND", "0.0.0.0")  # 监听所有网卡：Tailnet + 局域网（手机直连）
    srv = ThreadingHTTPServer((bind, port), HubHandler)
    srv.gateway = gw
    srv.registry = registry
    srv.store = store
    return srv


def _read_token_file() -> str:
    try:
        import sys as _sys

        _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from hub.secretbox import read_secret  # 与部署副本同目录
    except Exception:
        try:
            from secretbox import read_secret
        except Exception:
            return ""
    for p in (os.environ.get("PCH_TOKEN_FILE", ""), r"C:\ProgramData\firefly-bot\pch.token"):
        if not p:
            continue
        v = read_secret(p)
        if v:
            return v
    return ""


def main():
    token = os.environ.get("PCH_TOKEN", "") or _read_token_file()
    if token:
        os.environ["PCH_TOKEN"] = token
    else:
        sys.exit("PCH_TOKEN 未配置（环境变量或令牌文件）")
    if not os.environ.get("PCH_PHONE_TOKEN", ""):
        sys.exit("PCH_PHONE_TOKEN 未配置（环境变量）")
    store = HubStore(os.environ.get("PCH_DATA_DIR", str(Path.home() / "pch-data")))
    engine = StateEngine()
    reg = DeviceRegistry()
    port = int(os.environ.get("PCH_PORT", "8901"))
    srv = make_server(store, engine, reg, port)
    sys.stderr.write(f"[pch-hub] listening on 127.0.0.1:{port} (read-only)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":
    main()
