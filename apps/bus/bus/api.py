"""bus 入站 HTTP API（内部接口，CONTRACTS §0.1 方案②）。

- POST /api/v1/inbound/qq        qbot 适配器转发用户 QQ 消息 → inbox（source=qq）
- POST /api/v1/inbound/desktop   桌宠用户消息 → inbox（source=desktop；实际桌宠走 WS，HTTP 供测试/备用）
- POST /api/v1/inbound/mobile    手机用户消息 → inbox（source=mobile；契约占位，adapter 未实现）
- GET  /api/v1/health            探活

请求体：{"content": str, "refId"?: str, "meta"?: object}
响应：{"id": "<inbox message id>", "sequence": {...}, "policy": "fixed|first_reachable"}

鉴权：设置 BUS_TOKEN 环境变量后，POST 需携带 X-Bus-Token 头（恒定时间比较）。
"""
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from bus.input_bus import InputBus
from bus.models import MessageSource
from bus.store import BusStore

_SOURCE_BY_PATH = {
    "/api/v1/inbound/qq": MessageSource.QQ,
    "/api/v1/inbound/desktop": MessageSource.DESKTOP,
    "/api/v1/inbound/mobile": MessageSource.MOBILE,
}
_MAX_BODY = 64 * 1024


def _bus_token() -> str:
    return os.environ.get("BUS_TOKEN", "")


def _is_loopback(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1")


def _auth_ok(handler) -> bool:
    """X-Bus-Token 校验（🟠14 绑定收紧）。

    - 配置 BUS_TOKEN：恒定时间比较。
    - 未配置 BUS_TOKEN：仅放行本地回环请求（127.0.0.1/::1）——入站 API 不对外网开放，
      防止「无 token 完全放行」的静默暴露（内部进程默认本机访问语义显式化）。
    """
    expected = _bus_token()
    if expected:
        got = handler.headers.get("X-Bus-Token", "")
        return hmac.compare_digest(got, expected)
    return _is_loopback(handler.client_address[0])


class BusHttpHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > _MAX_BODY:
                return None
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/health":
            self._json(200, {"status": "ok", "service": "bus"})
        else:
            self._json(404, {"error": {"code": "NOT_FOUND", "message": "unknown endpoint"}})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in _SOURCE_BY_PATH:
            self._json(404, {"error": {"code": "NOT_FOUND", "message": "unknown endpoint"}})
            return
        if not _auth_ok(self):
            self._json(401, {"error": {"code": "UNAUTHORIZED", "message": "missing or bad X-Bus-Token"}})
            return
        body = self._read_body()
        if body is None:
            self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": "bad json or body > 64KB"}})
            return
        content = str(body.get("content", "")).strip()
        if not content:
            self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": "content required"}})
            return
        try:
            message = self.server.input_bus.receive(
                source=_SOURCE_BY_PATH[parsed.path],
                content=content,
                refId=body.get("refId"),
                meta=body.get("meta"),
            )
        except ValueError as e:  # source/kind 组合校验失败
            self._json(400, {"error": {"code": "INVALID_PAYLOAD", "message": str(e)}})
            return
        self._json(200, {
            "id": message.id,
            "source": message.source.value,
            "sequence": [t.value for t in self.server.store.get_inbound(message.id)["sequence"].targets],
            "policy": self.server.store.get_inbound(message.id)["policy"],
        })

    def log_message(self, fmt, *args):
        import sys

        sys.stderr.write("[bus-api] " + (fmt % args) + "\n")


def make_http_server(store: BusStore, input_bus: InputBus, port: int = 8766, bind: str = "127.0.0.1"):
    """创建入站 HTTP 服务（bus 进程内）。"""
    srv = ThreadingHTTPServer((bind, port), BusHttpHandler)
    srv.store = store
    srv.input_bus = input_bus
    return srv
