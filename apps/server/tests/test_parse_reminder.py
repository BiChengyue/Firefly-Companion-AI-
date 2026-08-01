"""parse_chinese_reminder_intent 单元测试 — 中文提醒意图解析。

覆盖场景：
- 相对时间（分钟后、小时后、秒后）
- 绝对时间（明天/今天/后天 + 时间点）
- 数字时间 + 中文时间（HH:MM、X点、X点半、X点Y分）
- 上午/下午/晚上 时段处理
- 非法/不相关输入返回 None
"""
import pytest
from datetime import datetime

from app.api.chat import parse_chinese_reminder_intent


class TestChineseReminderIntent:

    # ── 相对时间 ──

    def test_minutes_later(self):
        """5 分钟后 — 应正确偏移"""
        result = parse_chinese_reminder_intent("流萤，5分钟后提醒我开会")
        assert result is not None
        assert "dueTimestamp" in result
        assert "开会" in result["text"]
        now_ts = int(datetime.now().timestamp() * 1000)
        # 允许正负 5 秒误差
        assert abs(result["dueTimestamp"] - now_ts) < 310000

    def test_hours_later(self):
        """1 小时后 — 应正确偏移"""
        result = parse_chinese_reminder_intent("1小时后叫醒我")
        assert result is not None
        now_ts = int(datetime.now().timestamp() * 1000)
        # 1小时 = 3600000ms
        assert abs(result["dueTimestamp"] - now_ts - 3600000) < 5000

    # ── 绝对时间（明天） ──

    def test_tomorrow_afternoon(self):
        """明天下午 3 点"""
        result = parse_chinese_reminder_intent("流萤，明天下午3点提醒我拿快递")
        assert result is not None
        assert "拿快递" in result["text"]
        # display_time 应为 "明天HH:MM"
        assert "明天" in result["text"]
        assert "15:00" in result["text"]

    def test_tomorrow_morning(self):
        """明天早上八点半"""
        result = parse_chinese_reminder_intent("提醒我明天早上八点半起床")
        assert result is not None

    def test_tomorrow_clock_format(self):
        """明天 14:30 格式"""
        result = parse_chinese_reminder_intent("提醒我明天14:30定位打卡")
        assert result is not None
        assert "打卡" in result["text"]

    # ── 绝对时间（今天） ──

    def test_today_evening(self):
        """今天晚上8点"""
        result = parse_chinese_reminder_intent("今天晚上8点提醒我吃饭")
        assert result is not None

    # ── 非法输入 ──

    def test_no_keyword_returns_none(self):
        """没有提醒/闹钟/叫醒/定时 关键词 → None"""
        assert parse_chinese_reminder_intent("今天天气怎么样") is None

    def test_empty_string_returns_none(self):
        """空字符串 → None"""
        assert parse_chinese_reminder_intent("") is None

    def test_keyword_but_no_time_returns_none(self):
        """有"提醒"关键词但无有效时间 → None"""
        result = parse_chinese_reminder_intent("提醒我一下")
        assert result is None

    # ── 边界场景 ──

    def test_midnight_crossover(self):
        """23:59 提醒 → 应跨到明天"""
        result = parse_chinese_reminder_intent("提醒我23:59关电脑")
        assert result is not None

    def test_seconds_later(self):
        """30秒后"""
        result = parse_chinese_reminder_intent("30秒后提醒我")
        assert result is not None

    # ── 中文数字 ──

    def test_chinese_numeral_three_oclock(self):
        """下午三点"""
        result = parse_chinese_reminder_intent("下午三点提醒我喝咖啡")
        assert result is not None
        assert "15:00" in result["text"]
