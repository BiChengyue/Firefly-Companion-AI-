"""模拟器 + Context 网关 + qbot 降级测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.hub.contracts import is_valid
from src.hub.context_gateway import ContextGateway
from src.hub.ingress import DeviceRegistry, sign_payload
from src.hub.simulator import Simulator
from src.hub.state_engine import StateEngine
from src.hub.storage import HubStore


def test_simulator_full_flow(tmp_path):
    store = HubStore(tmp_path)
    engine = StateEngine()
    reg = DeviceRegistry()
    dev_id, secret = reg.register("sim-pc", ["presence"])
    sim = Simulator(store, engine, reg)

    # 合法事件（带签名）
    ev = sim.make_event(dev_id, "screen_lock")
    ev["_sig"] = sign_payload(ev, secret, ev["occurred_at"])
    assert sim.feed(dev_id, secret, ev) is True

    # 无签名事件被拒
    ev2 = sim.make_event(dev_id, "screen_unlock")
    assert sim.feed(dev_id, secret, ev2) is False

    # 篡改 payload 后签名不符被拒
    ev3 = sim.make_event(dev_id, "screen_unlock")
    ev3["_sig"] = sign_payload(ev3, secret, ev3["occurred_at"])
    ev3["payload"] = {"evil": True}
    assert sim.feed(dev_id, secret, ev3) is False

    assert store.get_life_state()["state"] in {"sleeping", "leisure", "busy", "gaming", "commuting", "eating", "just_woke", "unknown"}


def test_context_gateway_output_valid_and_safe(tmp_path):
    store = HubStore(tmp_path)
    engine = StateEngine()
    engine.ingest({"event_type": "screen_lock", "occurred_at": _now_iso()})
    gw = ContextGateway(store, engine)
    ctx = gw.build("ctx-1")
    assert ctx is not None
    assert is_valid("context", ctx)
    # 安全性：上下文不得携带精确坐标/原始数据
    assert "lat" not in str(ctx) and "lon" not in str(ctx)
    assert ctx["expires_at"] > ctx["issued_at"]


def test_context_with_facts(tmp_path):
    store = HubStore(tmp_path)
    engine = StateEngine()
    gw = ContextGateway(store, engine)
    now = _now_iso()
    store.put_fact("weather", "shanghai", "晴 26°C", 0.9, now, source="open-meteo")
    ctx = gw.build("ctx-2")
    assert ctx and any(f["kind"] == "weather" for f in ctx["facts"])


def test_qbot_no_hub_dependency():
    """降级保证：firefly-bot 的 qbot 不得 import 控制中心（Hub 挂了 qbot 仍可聊）。"""
    qbot_src = (Path(__file__).resolve().parent.parent.parent / "firefly-bot" / "bot" / "qbot.py")
    if qbot_src.exists():
        text = qbot_src.read_text(encoding="utf-8", errors="ignore")
        assert "personal_control_hub" not in text
        assert "from src.hub" not in text
        assert "import hub" not in text


def test_scope_denied_event(tmp_path):
    """事件 scope 不在设备授权域内 → 拒绝。"""
    store = HubStore(tmp_path)
    engine = StateEngine()
    reg = DeviceRegistry()
    dev_id, secret = reg.register("sim-pc", ["presence"])  # 只授权 presence
    sim = Simulator(store, engine, reg)
    ev = sim.make_event(dev_id, "screen_lock")
    ev["scope"] = "calendar"  # 未授权 scope
    ev["_sig"] = sign_payload(ev, secret, ev["occurred_at"])
    assert sim.feed(dev_id, secret, ev) is False
    assert any(a["target"] == dev_id and "scope" in a["detail"] for a in store.recent_audit())


def test_context_facts_whitelist(tmp_path):
    """context 只放行白名单 kind；非白名单/超长 fact 被过滤。"""
    store = HubStore(tmp_path)
    engine = StateEngine()
    gw = ContextGateway(store, engine)
    now = _now_iso()
    store.put_fact("weather", "sh", "晴", 0.9, now, source="open-meteo")
    store.put_fact("secret", "password", "hunter2", 1.0, now, source="leak")  # 非白名单 kind
    store.put_fact("note", "long", "x" * 600, 0.5, now, source="user")        # 超长
    ctx = gw.build("ctx-3")
    assert ctx is not None
    kinds = {f["kind"] for f in ctx["facts"]}
    assert "weather" in kinds
    assert "secret" not in kinds
    assert "note" not in kinds  # 超长被过滤


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
