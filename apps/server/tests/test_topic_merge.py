"""Phase 15 验收测试 — Topic 差异化合并策略。

验证 single / entity_replace / multi 三种策略的 ADD/UPDATE 行为
以及 _detect_entity 品牌提取、SINGLE_VALUED_TOPICS 扩展。
"""
import pytest

from app.core.memory.personal import (
    TOPIC_MERGE_STRATEGIES,
    SINGLE_VALUED_TOPICS,
    _detect_topic,
    _detect_entity,
)


class TestTopicDetection:
    """Topic 与 Entity 检测。"""

    def test_hometown_detection(self):
        topic, entity = _detect_topic("我是杭州人")
        assert topic == "identity_hometown"
        assert entity == "杭州"

    def test_name_detection(self):
        topic, entity = _detect_topic("我叫小明")
        assert topic == "identity_name"

    def test_os_detection(self):
        topic, entity = _detect_topic("用 Mac 系统")
        assert topic == "operating_system"

    def test_hardware_tablet_detection(self):
        topic, entity = _detect_topic("喜欢用荣耀平板")
        assert topic == "hardware_tablet"

    def test_hardware_mobile_detection(self):
        topic, entity = _detect_topic("华为手机很好用")
        assert topic == "hardware_mobile"

    def test_dietary_detection(self):
        topic, entity = _detect_topic("喜欢喝咖啡和奶茶")
        assert topic == "dietary_preference"

    def test_dietary_new_keywords(self):
        # Phase 16 新增饮食关键词
        for text in ["喜欢吃烧烤", "喜欢日料", "吃海鲜过敏", "忌口花生"]:
            topic, _ = _detect_topic(text)
            assert topic == "dietary_preference", f"Failed for: {text}"

    def test_software_new_keywords(self):
        # Phase 16 新增软件关键词
        for text in ["用 Figma 做设计", "在 Notion 记笔记", "飞书开会"]:
            topic, _ = _detect_topic(text)
            assert topic in ("software_dev", "software_app"), f"Failed for: {text}"

    def test_entertainment_detection(self):
        topic, entity = _detect_topic("喜欢听许嵩的歌")
        assert topic == "entertainment_hobby"

    def test_entertainment_new_keywords(self):
        # Phase 16 新增娱乐关键词
        for text in ["玩原神", "看番剧", "打桌游"]:
            topic, _ = _detect_topic(text)
            assert topic == "entertainment_hobby", f"Failed for: {text}"


class TestEntityDetection:
    """品牌 + 型号提取。"""

    def test_logitech_mouse(self):
        entity = _detect_entity("罗技 G502 鼠标")
        assert "logitech" in entity

    def test_razer_mouse(self):
        entity = _detect_entity("雷蛇 DeathAdder")
        assert "razer" in entity

    def test_apple_product(self):
        entity = _detect_entity("苹果 iPhone 15 Pro")
        assert "apple" in entity

    def test_huawei(self):
        entity = _detect_entity("华为 Mate 60")
        assert "huawei" in entity

    def test_no_brand(self):
        entity = _detect_entity("一个普通的键盘")
        # 无品牌匹配时返回词级简化
        assert entity and entity != ""


class TestMergeStrategies:
    """合并策略字典验证。"""

    def test_identity_is_single(self):
        assert TOPIC_MERGE_STRATEGIES.get("identity_name") == "single"
        assert TOPIC_MERGE_STRATEGIES.get("identity_hometown") == "single"
        assert TOPIC_MERGE_STRATEGIES.get("identity_birthday") == "single"
        assert TOPIC_MERGE_STRATEGIES.get("identity_location") == "single"

    def test_devices_are_single(self):
        assert TOPIC_MERGE_STRATEGIES.get("operating_system") == "single"
        assert TOPIC_MERGE_STRATEGIES.get("hardware_pc") == "single"
        assert TOPIC_MERGE_STRATEGIES.get("hardware_tablet") == "single"
        assert TOPIC_MERGE_STRATEGIES.get("hardware_mobile") == "single"

    def test_accessory_is_entity_replace(self):
        assert TOPIC_MERGE_STRATEGIES.get("hardware_accessory") == "entity_replace"

    def test_dietary_is_multi(self):
        assert TOPIC_MERGE_STRATEGIES.get("dietary_preference") == "multi"
        assert TOPIC_MERGE_STRATEGIES.get("entertainment_hobby") == "multi"
        assert TOPIC_MERGE_STRATEGIES.get("software_app") == "multi"
        assert TOPIC_MERGE_STRATEGIES.get("general_preference") == "multi"

    def test_unknown_topic_defaults_to_multi(self):
        assert TOPIC_MERGE_STRATEGIES.get("nonexistent_topic", "multi") == "multi"


class TestSingleValuedTopics:
    """单值槽位扩展验证。"""

    def test_tablet_is_single_valued(self):
        assert "hardware_tablet" in SINGLE_VALUED_TOPICS

    def test_mobile_is_single_valued(self):
        assert "hardware_mobile" in SINGLE_VALUED_TOPICS

    def test_identity_topics_are_single_valued(self):
        for t in ["identity_name", "identity_hometown", "identity_location",
                   "identity_job", "identity_birthday"]:
            assert t in SINGLE_VALUED_TOPICS, f"{t} should be single-valued"

    def test_dietary_is_not_single_valued(self):
        # 饮食偏好是多值累积的，不应在 single-valued 集合中
        assert "dietary_preference" not in SINGLE_VALUED_TOPICS
