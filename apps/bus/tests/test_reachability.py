"""可达性追踪测试：10s 上报 × 3 次置信、心跳超时兜底（CONTRACTS §3.1 / D-2）。"""
import time

from bus.reachability import ReachabilityTracker


def test_initial_state_all_offline():
    assert ReachabilityTracker().current().desktopOnline is False


def test_single_heartbeat_not_enough():
    """单次上报不采信（防抖动）：需连续 3 次一致。"""
    t = ReachabilityTracker()
    t.report_desktop(True)
    assert t.current().desktopOnline is False


def test_three_heartbeats_confirm_online():
    t = ReachabilityTracker()
    now = 1000.0
    for _ in range(3):
        t.report_desktop(True, now=now)
        now += 10
    assert t.current(now=now).desktopOnline is True


def test_flapping_not_confirmed():
    """抖动（真真假假）不采信。"""
    t = ReachabilityTracker()
    now = 1000.0
    for v in (True, False, True):
        t.report_desktop(v, now=now)
        now += 10
    assert t.current(now=now).desktopOnline is False


def test_three_offline_reports_confirm_offline():
    t = ReachabilityTracker()
    now = 1000.0
    for _ in range(3):
        t.report_desktop(True, now=now)
        now += 10
    assert t.current(now=now).desktopOnline is True
    for _ in range(3):
        t.report_desktop(False, now=now)
        now += 10
    assert t.current(now=now).desktopOnline is False


def test_stale_timeout_forces_offline():
    """心跳超时兜底：断连后 30s（3 周期）判离线，即使趋势未确认。"""
    t = ReachabilityTracker()
    now = 1000.0
    t.report_desktop(True, now=now)
    # 只上报一次（在线但未达 3 次置信）；31s 后仍判离线（超时兜底）
    assert t.current(now=now + 31).desktopOnline is False


def test_recent_heartbeat_keeps_online():
    t = ReachabilityTracker()
    now = 1000.0
    for _ in range(3):
        t.report_desktop(True, now=now)
        now += 10
    assert t.current(now=now).desktopOnline is True
    # 20s 后仍在超时窗口内
    assert t.current(now=now + 20).desktopOnline is True
    # 31s 后超时
    assert t.current(now=now + 31).desktopOnline is False


def test_mobile_placeholder_always_false():
    """手机端契约占位（§0.2）：mobileOnline/mobileForeground 恒 False。"""
    t = ReachabilityTracker()
    for _ in range(3):
        t.report_desktop(True)
    r = t.current()
    assert r.mobileOnline is False
    assert r.mobileForeground is False
