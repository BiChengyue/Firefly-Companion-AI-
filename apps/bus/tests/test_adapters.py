"""通道适配器测试：qq 限频/档位/critical 绕过；desktop 推送协议；MultiAdapter 分发。"""
import pytest

from bus.adapters import DesktopAdapter, MobileAdapter, MultiAdapter, QqAdapter, QqRateLimiter
from bus.models import DeliveryChannel, OutboundMessage


def _out(target, content="hello", critical=False, ref_id=None, action=None):
    return OutboundMessage(id="m1", target=target, content=content, critical=critical, refId=ref_id, action=action)


# ── QQ 限频 ──

class FakeClock:
    def __init__(self, start=1000000.0):
        self.now = start

    def __call__(self):
        return self.now


def test_qq_rate_limiter_daily_hourly():
    clock = FakeClock()
    limiter = QqRateLimiter(daily=3, hourly=2, now=clock)
    assert limiter.check() is True
    limiter.record()
    assert limiter.check() is True
    limiter.record()
    assert limiter.check() is False  # 小时限 2 已满
    assert limiter.check(critical=True) is True  # critical 绕过
    # 跨小时重置
    clock.now += 3600
    assert limiter.check() is True


def test_qq_rate_limiter_trip_daily_fuse():
    """AI-5 审查项：平台限频当日熔断（trip_daily）——当日 check 拒绝，次日恢复。"""
    clock = FakeClock()
    limiter = QqRateLimiter(daily=5, hourly=5, now=clock)
    limiter.trip_daily()
    assert limiter.check() is False       # 当日熔断
    assert limiter.check(critical=True) is True  # critical 仍绕过（§3）
    clock.now += 86400
    assert limiter.check() is True        # 次日恢复


def test_qq_adapter_platform_limit_trips_daily():
    """429/平台限频 → 当日熔断，后续 check 拒绝。"""
    class PlatformLimited(Exception):
        pass

    def limited(t, c):
        import urllib.error

        raise urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)

    limiter = QqRateLimiter(daily=10, hourly=10)
    adapter = QqAdapter(appid="a", secret="s", openid="o", limiter=limiter,
                        send_fn=limited, token_fn=lambda a, s: "tok")
    assert adapter.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ)) is False
    assert limiter.check() is False  # 已熔断


def test_qq_adapter_limit_hit_returns_false():
    limiter = QqRateLimiter(daily=1, hourly=1)
    sent = []
    adapter = QqAdapter(appid="a", secret="s", openid="o", limiter=limiter, send_fn=lambda t, c: sent.append(c), token_fn=lambda a, s: "tok")
    assert adapter.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ)) is True
    assert len(sent) == 1
    assert adapter.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ)) is False  # 限频拒绝


def test_qq_adapter_critical_bypasses_limit():
    limiter = QqRateLimiter(daily=1, hourly=1)
    sent = []
    adapter = QqAdapter(appid="a", secret="s", openid="o", limiter=limiter, send_fn=lambda t, c: sent.append(c), token_fn=lambda a, s: "tok")
    assert adapter.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ)) is True
    assert adapter.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ, critical=True)) is True  # critical 绕过
    assert len(sent) == 2


def test_qq_adapter_tier_truncation():
    limiter = QqRateLimiter(daily=10, hourly=10)
    sent = []
    adapter = QqAdapter(appid="a", secret="s", openid="o", limiter=limiter, tier="normal", send_fn=lambda t, c: sent.append(c), token_fn=lambda a, s: "tok")
    long_content = "x" * 200
    adapter.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ, content=long_content))
    assert len(sent[0]) == 80  # normal 档位上限 80


def test_qq_adapter_wrong_channel_false():
    adapter = QqAdapter(appid="a", secret="s", openid="o", send_fn=lambda t, c: None, token_fn=lambda a, s: "tok")
    assert adapter.deliver(DeliveryChannel.DESKTOP, _out(DeliveryChannel.DESKTOP)) is False


def test_qq_adapter_send_error_returns_false():
    def boom(t, c):
        raise RuntimeError("network")

    adapter = QqAdapter(appid="a", secret="s", openid="o", send_fn=boom, token_fn=lambda a, s: "tok")
    assert adapter.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ)) is False


# ── desktop 推送协议 ──

class FakeHub:
    def __init__(self):
        self.sent: list[dict] = []
        self.online_ = False

    def online(self):
        return self.online_

    def push(self, message: dict) -> bool:
        if not self.online_:
            return False
        self.sent.append(message)
        return True


def test_desktop_adapter_pushes_proactive_speech():
    hub = FakeHub()
    hub.online_ = True
    adapter = DesktopAdapter(hub)
    assert adapter.deliver(DeliveryChannel.DESKTOP, _out(DeliveryChannel.DESKTOP, content="到家啦", ref_id="ev-1")) is True
    assert hub.sent[0] == {"type": "proactive_speech", "content": "到家啦", "source": "bus", "refId": "ev-1"}


def test_desktop_adapter_offline_returns_false():
    hub = FakeHub()
    adapter = DesktopAdapter(hub)
    assert adapter.deliver(DeliveryChannel.DESKTOP, _out(DeliveryChannel.DESKTOP)) is False
    assert hub.sent == []


def test_desktop_adapter_pushes_voice_and_action():
    from bus.models import DeviceAction, DeviceCommandKind, OutboundVoice

    hub = FakeHub()
    hub.online_ = True
    adapter = DesktopAdapter(hub)
    msg = OutboundMessage(
        id="m9", target=DeliveryChannel.DESKTOP, content="我帮你打开导航",
        voice=OutboundVoice(audioUrl="http://x/1.wav", text="我帮你打开导航"),
        action=DeviceAction(kind=DeviceCommandKind.OPEN_APP, payload={"app": "maps"}),
    )
    assert adapter.deliver(DeliveryChannel.DESKTOP, msg) is True
    types = [s["type"] for s in hub.sent]
    assert types == ["proactive_speech", "voice_audio", "device_command"]
    assert hub.sent[1]["audioUrl"] == "http://x/1.wav"
    assert hub.sent[2]["command"]["kind"] == "open_app"


def test_desktop_adapter_wrong_channel_false():
    adapter = DesktopAdapter(FakeHub())
    assert adapter.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ)) is False


# ── mobile 占位 ──

def test_mobile_adapter_placeholder():
    adapter = MobileAdapter()
    assert adapter.deliver(DeliveryChannel.MOBILE_INAPP, _out(DeliveryChannel.MOBILE_INAPP)) is False
    assert adapter.deliver(DeliveryChannel.MOBILE_NOTIFY, _out(DeliveryChannel.MOBILE_NOTIFY)) is False


# ── MultiAdapter 分发 ──

def test_multi_adapter_routes_by_channel():
    hub = FakeHub()
    hub.online_ = True
    qq = QqAdapter(appid="a", secret="s", openid="o", send_fn=lambda t, c: None, token_fn=lambda a, s: "tok")
    multi = MultiAdapter([DesktopAdapter(hub), qq, MobileAdapter()])
    assert multi.deliver(DeliveryChannel.DESKTOP, _out(DeliveryChannel.DESKTOP)) is True
    assert multi.deliver(DeliveryChannel.QQ, _out(DeliveryChannel.QQ)) is True
    assert multi.deliver(DeliveryChannel.MOBILE_INAPP, _out(DeliveryChannel.MOBILE_INAPP)) is False
