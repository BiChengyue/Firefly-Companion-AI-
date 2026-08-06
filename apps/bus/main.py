"""bus 独立进程入口（CONTRACTS §0.1 方案②）。

启动：cd apps/bus && python main.py
组件：
- 入站 HTTP API（qq/desktop/mobile 消息 → inbox）
- 桌宠 WS 服务（/ws/desktop：用户消息 + 心跳 + 推送）
- 调度循环（消费 inbox → 调 companion 生成 → outbox → 派发）
- 事件桥（轮询 hub-api events → 构造 hub_event 入 inbox）

环境变量：
  BUS_DB_PATH      bus.db 路径（默认 apps/bus/data/bus.db）
  BUS_TOKEN        入站 API 鉴权 token（可选）
  BUS_PORT/BUS_BIND  入站 HTTP（默认 8766 / 127.0.0.1）
  BUS_WS_PORT      桌宠 WS（默认 8767）
  COMPANION_WS_URL companion /ws/chat 地址（默认 ws://127.0.0.1:8765/ws/chat）
  PCH_API_URL / PCH_TOKEN  hub-api 地址与 token（事件桥）
  QBOT_APPID / QBOT_SECRET / QBOT_OPENID  QQ 发送凭据
"""
import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bus.adapters import DesktopAdapter, MobileAdapter, MultiAdapter, QqAdapter  # noqa: E402
from bus.api import make_http_server  # noqa: E402
from bus.companion_bridge import CompanionBridge  # noqa: E402
from bus.dispatcher import Dispatcher  # noqa: E402
from bus.event_bridge import EventBridge  # noqa: E402
from bus.input_bus import InputBus  # noqa: E402
from bus.reachability import ReachabilityTracker  # noqa: E402
from bus.scheduler import Scheduler  # noqa: E402
from bus.store import BusStore  # noqa: E402
from bus.ws_server import DesktopHub, start_desktop_ws_thread  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
_log = logging.getLogger("bus.main")


def main():
    store = BusStore()
    input_bus = InputBus(store)
    tracker = ReachabilityTracker()
    hub = DesktopHub(tracker)

    adapter = MultiAdapter([
        DesktopAdapter(hub),
        QqAdapter(),
        MobileAdapter(),
    ])
    dispatcher = Dispatcher(store, adapter)
    bridge = CompanionBridge()
    scheduler = Scheduler(store, bridge, dispatcher, tracker)

    # 入站 HTTP API（qbot 适配器 / 备用入口）
    http_port = int(os.environ.get("BUS_PORT", "8766"))
    http_bind = os.environ.get("BUS_BIND", "127.0.0.1")
    http_srv = make_http_server(store, input_bus, port=http_port, bind=http_bind)
    threading.Thread(target=http_srv.serve_forever, name="bus-http", daemon=True).start()
    _log.info("inbound http on %s:%s", http_bind, http_port)

    # 桌宠 WS 服务（用户消息 + 心跳 + mode_switch + 推送）
    ws_port = int(os.environ.get("BUS_WS_PORT", "8767"))
    start_desktop_ws_thread(tracker, input_bus, mode_switch_fn=bridge.switch_mode_sync, hub=hub, port=ws_port)
    _log.info("desktop ws on :%s/ws/desktop", ws_port)

    # 事件桥（hub 轮询）+ 调度循环
    EventBridge(input_bus).start_thread()
    scheduler.start_thread()
    _log.info("bus started (db=%s)", os.environ.get("BUS_DB_PATH", "apps/bus/data/bus.db"))

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        _log.info("bus stopping")
        hub.close_all()
        http_srv.shutdown()


if __name__ == "__main__":
    main()
