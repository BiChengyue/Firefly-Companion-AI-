"""QQ 通道适配器（CONTRACTS §2/§3）：兜底输出，限频 + 限尺度 + critical 绕过。

- DAILY_LIMIT / HOURLY_LIMIT（保险丝，同旧 event_worker）
- 档位 normal/ambiguous（发送前长度兜底截断；正常由生成侧 QQ 协议保证尺度）
- critical 绕过限频（§3：低电量等）
- 发送：QQ 官方 API（getAppAccessToken → users/{openid}/messages），失败返回 False（派发器降级）
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request

from bus.models import DeliveryChannel, OutboundMessage

_log = logging.getLogger("bus.qq")

DAILY_LIMIT = int(os.environ.get("QQ_DAILY_LIMIT", "100"))
HOURLY_LIMIT = int(os.environ.get("QQ_HOURLY_LIMIT", "10"))
TIER_MAX_LEN = {"normal": 80, "ambiguous": 120}


class QqRateLimiter:
    """QQ 发送限频（日/小时双保险丝，内存计数按日期/小时键重置）。"""

    def __init__(self, daily: int = DAILY_LIMIT, hourly: int = HOURLY_LIMIT, now=None):
        self.daily = daily
        self.hourly = hourly
        self._now = now or time.time
        self._reset()

    def _reset(self):
        now = self._now()
        self._day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        self._hour_key = time.strftime("%Y-%m-%d %H", time.localtime(now))
        self._sent_day = 0
        self._sent_hour = 0

    def check(self, critical: bool = False) -> bool:
        """非 critical 且超限 → False（拒绝）；critical 恒放行（§3）。"""
        if critical:
            return True
        now = self._now()
        day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        hour_key = time.strftime("%Y-%m-%d %H", time.localtime(now))
        if day_key != self._day_key or hour_key != self._hour_key:
            self._reset()
        return self._sent_day < self.daily and self._sent_hour < self.hourly

    def record(self, now=None):
        now = now or self._now()
        day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        hour_key = time.strftime("%Y-%m-%d %H", time.localtime(now))
        if day_key != self._day_key or hour_key != self._hour_key:
            self._reset()
        self._sent_day += 1
        self._sent_hour += 1

    def trip_daily(self, now=None):
        """平台限频熔断：当日配额打满，停止今日再试（防反复撞墙）。"""
        now = now or self._now()
        day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        if day_key != self._day_key:
            self._reset()
        self._sent_day = self.daily

    def stats(self) -> dict:
        return {"day": self._day_key, "sent_day": self._sent_day, "sent_hour": self._sent_hour}


def _qq_access_token(appid: str, secret: str) -> str:
    """QQ 官方 access_token（getAppAccessToken）。"""
    req = urllib.request.Request(
        "https://bots.qq.com/app/getAppAccessToken",
        data=json.dumps({"appId": appid, "clientSecret": secret}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read().decode())
    return d["access_token"]


def _qq_send(token: str, openid: str, content: str) -> None:
    """发送文字消息（msg_type=0）。"""
    req = urllib.request.Request(
        f"https://api.bot.qq.com/v2/users/{openid}/messages",
        data=json.dumps({"content": content, "msg_type": 0}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"QQBot {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


class QqAdapter:
    """QQ 派发通道：限频 + 档位截断 + critical 绕过 + 发送。"""

    def __init__(
        self,
        appid: str = "",
        secret: str = "",
        openid: str = "",
        limiter: QqRateLimiter | None = None,
        tier: str = "normal",
        send_fn=None,
        token_fn=None,
    ):
        self.appid = appid or os.environ.get("QBOT_APPID", "")
        self.secret = secret or os.environ.get("QBOT_SECRET", "")
        self.openid = openid or os.environ.get("QBOT_OPENID", "")
        self.limiter = limiter or QqRateLimiter()
        self.tier = tier if tier in TIER_MAX_LEN else "normal"
        self._send_fn = send_fn or self._default_send
        self._token_fn = token_fn or _qq_access_token
        self._token = ""
        self._token_exp = 0.0

    @staticmethod
    def channels():
        return [DeliveryChannel.QQ]

    def _default_send(self, token: str, content: str):
        _qq_send(token, self.openid, content)

    def _get_token(self) -> str:
        if self._token and self._token_exp > time.time() + 300:
            return self._token
        self._token = self._token_fn(self.appid, self.secret)
        self._token_exp = time.time() + 2300  # 官方 expires_in≈7200，保守 2300s
        return self._token

    def deliver(self, channel: DeliveryChannel, message: OutboundMessage) -> bool:
        """仅处理 qq 通道；限频拒绝/发送失败 → False（派发器继续降级）。"""
        if channel != DeliveryChannel.QQ:
            return False
        if not self.limiter.check(critical=message.critical):
            _log.info("qq limit hit (critical=%s)", message.critical)
            return False
        if not (self.appid and self.secret and self.openid):
            _log.warning("qq adapter not configured (appid/secret/openid)")
            return False
        # 档位长度兜底截断（生成侧尺度优先，adapter 只做保险）
        # T-27 A：截断尊重换行边界——超长时优先在最近换行处截断，避免切在句中丢语义。
        max_len = TIER_MAX_LEN[self.tier]
        # T-30 日报长文：refId=report-*（主动日报）→ 走 QQ 单条上限（官方 4000），
        # 不按对话档位 80 截断——否则六板块日报被截到第一板块。普通对话仍按档位限尺度。
        if message.refId and message.refId.startswith("report-"):
            max_len = 4000
        # T-30 排查日志：确认日报豁免是否生效（发后删除或降 debug）
        _log.info("qq deliver refId=%s content_len=%d max_len=%d", message.refId, len(message.content), max_len)
        content = message.content
        if len(content) > max_len:
            cut = content[:max_len]
            nl = cut.rfind("\n")
            content = cut[:nl] if nl > 0 else cut
        try:
            token = self._get_token()
            self._send_fn(token, content)
            self.limiter.record()
            return True
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:200]
            _log.warning("qq send http %s: %s", e.code, err)
            # 平台限频（429/304049/304050/11253）→ 当日熔断（防反复撞墙）
            if e.code == 429 or "304049" in err or "304050" in err or '"err_code": 11253' in err:
                self.limiter.trip_daily()
            return False
        except Exception as e:
            _log.warning("qq send error: %s", e)
            return False
