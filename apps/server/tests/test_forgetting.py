"""Phase 13 验收测试 — 六维度遗忘体系。

验证: 差异化衰减率/阈值、时效分级、竞争衰减、记忆刷新、新设备优先。
"""
import pytest

from app.core.memory.personal import (
    TOPIC_DECAY_RATES,
    TOPIC_DECAY_THRESHOLDS,
    COMPETITIVE_DECAY_TOPICS,
    DEFAULT_DECAY_RATE,
    DEFAULT_DECAY_THRESHOLD,
    _normalize_topic,
    PersonalMemoryManager,
)


class TestDecayRates:
    """维度一：差异化衰减率。"""

    def test_identity_near_immortal(self):
        """身份信息衰减极慢。"""
        for t in ["identity_name", "identity_hometown", "identity_birthday"]:
            assert TOPIC_DECAY_RATES[t] >= 0.99, f"{t} decay should be >= 0.99"

    def test_accessory_decays_fast(self):
        """外设衰减最快。"""
        assert TOPIC_DECAY_RATES["hardware_accessory"] == 0.85

    def test_entertainment_decays_fairly_fast(self):
        """兴趣/娱乐衰减较快。"""
        assert TOPIC_DECAY_RATES["entertainment_hobby"] <= 0.92

    def test_all_15_topics_covered(self):
        assert len(TOPIC_DECAY_RATES) == 15

    def test_unknown_topic_has_default(self):
        assert DEFAULT_DECAY_RATE == 0.92


class TestDecayThresholds:
    """维度二：差异化衰减阈值。"""

    def test_identity_threshold_very_low(self):
        """身份信息阈值极低——几乎不过滤。"""
        for t in ["identity_name", "identity_hometown", "identity_birthday",
                   "identity_location", "identity_job"]:
            assert TOPIC_DECAY_THRESHOLDS[t] == 0.10, f"{t} threshold should be 0.10"

    def test_accessory_threshold_highest(self):
        """外设阈值最高——最快退出注入。"""
        assert TOPIC_DECAY_THRESHOLDS["hardware_accessory"] == 0.40

    def test_standard_threshold(self):
        """标准阈值。"""
        assert TOPIC_DECAY_THRESHOLDS.get("dietary_preference") == 0.35
        assert TOPIC_DECAY_THRESHOLDS.get("general_preference") == 0.35

    def test_default_threshold(self):
        assert DEFAULT_DECAY_THRESHOLD == 0.35


class TestCompetitiveDecay:
    """维度四：竞争衰减 topic 集合。"""

    def test_accessory_triggers_competitive_decay(self):
        assert "hardware_accessory" in COMPETITIVE_DECAY_TOPICS

    def test_mobile_triggers_competitive_decay(self):
        assert "hardware_mobile" in COMPETITIVE_DECAY_TOPICS

    def test_identity_does_not_trigger(self):
        for t in ["identity_name", "identity_hometown"]:
            assert t not in COMPETITIVE_DECAY_TOPICS

    def test_dietary_does_not_trigger(self):
        assert "dietary_preference" not in COMPETITIVE_DECAY_TOPICS


class TestStalenessTiers:
    """维度三：时效语言分级。"""

    def test_active_tier(self):
        tier = PersonalMemoryManager._get_staleness_tier(3)
        assert tier == "active"

    def test_recent_tier(self):
        tier = PersonalMemoryManager._get_staleness_tier(50)
        assert tier == "recent"

    def test_stale_tier(self):
        tier = PersonalMemoryManager._get_staleness_tier(200)
        assert tier == "stale"

    def test_archived_tier(self):
        tier = PersonalMemoryManager._get_staleness_tier(400)
        assert tier == "archived"

    def test_boundary_7_days(self):
        assert PersonalMemoryManager._get_staleness_tier(6) == "active"
        assert PersonalMemoryManager._get_staleness_tier(7) == "recent"

    def test_boundary_90_days(self):
        assert PersonalMemoryManager._get_staleness_tier(89) == "recent"
        assert PersonalMemoryManager._get_staleness_tier(90) == "stale"

    def test_boundary_365_days(self):
        assert PersonalMemoryManager._get_staleness_tier(364) == "stale"
        assert PersonalMemoryManager._get_staleness_tier(365) == "archived"


class TestFormatMemoriesForPrompt:
    """维度三：时效语言注入格式。"""

    def test_formats_active_memory(self):
        sample = [
            {"content": "使用 Mac 电脑", "type": "偏好",
             "topic": "operating_system", "confidence": 0.95,
             "_days_since_access": 3},
        ]
        result = PersonalMemoryManager._format_memories_for_prompt(sample)
        assert "目前" in result
        assert "使用 Mac 电脑" in result

    def test_formats_stale_memory(self):
        sample = [
            {"content": "用过罗技鼠标", "type": "偏好",
             "topic": "hardware_accessory", "confidence": 0.65,
             "_days_since_access": 150},
        ]
        result = PersonalMemoryManager._format_memories_for_prompt(sample)
        assert "此前" in result

    def test_archived_not_injected(self):
        sample = [
            {"content": "古老记忆", "type": "偏好",
             "topic": "general_preference", "confidence": 0.50,
             "_days_since_access": 400},
        ]
        result = PersonalMemoryManager._format_memories_for_prompt(sample)
        assert result == "" or "古老" not in result

    def test_groups_by_topic(self):
        sample = [
            {"content": "杭州人", "type": "身份",
             "topic": "identity_hometown", "confidence": 0.95,
             "_days_since_access": 5},
            {"content": "喜欢咖啡", "type": "偏好",
             "topic": "dietary_preference", "confidence": 0.85,
             "_days_since_access": 10},
        ]
        result = PersonalMemoryManager._format_memories_for_prompt(sample)
        assert "杭州人" in result
        assert "喜欢咖啡" in result


class TestTopicNormalization:
    """Topic 名归一化兼容。"""

    def test_normalize_identity_hometown(self):
        assert _normalize_topic("identity_hometown") == "identity_hometown"

    def test_normalize_legacy_location_hometown(self):
        assert _normalize_topic("location_hometown") == "identity_hometown"

    def test_normalize_none(self):
        assert _normalize_topic(None) == "general_preference"

    def test_normalize_empty(self):
        assert _normalize_topic("") == "general_preference"

    def test_normalize_unchanged(self):
        assert _normalize_topic("dietary_preference") == "dietary_preference"
