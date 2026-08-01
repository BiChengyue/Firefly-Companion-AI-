"""个人长期记忆管理器 (Personal Memory Manager)。

专注于个人人设、偏好、关怀、短期 Buffer 缓冲与日常/工作命名空间隔离。
数据持久化在 SQLite `memories` 表。
"""

import json
import uuid
from typing import Optional, Any, Callable

import numpy as np

from app.config import get_settings
from app.core import db as _db
from app.core.logging_config import get_logger

logger = get_logger("memory.personal")
from app.core.llm.base import LLMMessage
from app.core.memory.embedding import (
    OnnxEmbeddingEngine,
    blob_to_vector,
    cosine_similarity,
    get_embedding_engine,
    get_hash_engine,
    vector_to_blob,
)


# 确定性通用属性特征词阵列（用于阶段二规则防守网）
# ⚠️ 严禁放入「喜欢/偏好/爱/音乐/游戏」等泛名词 —— 单独出现极易把闲聊误路由写为通用属性
UNIVERSAL_KEYWORDS = {
    # 设备 / 系统 / 数字环境（具体品牌型号名词）
    "电脑", "mac", "macbook", "windows", "linux", "macos", "ubuntu", "笔记本", "台式机",
    "手机", "android", "ios", "操作系统", "硬件", "设备",
    "平板", "ipad", "荣耀", "显示器", "键盘", "耳机", "鼠标",
    "编辑器", "ide", "vscode", "vs code", "jetbrains", "终端", "terminal",
    "编程语言", "浏览器", "chrome", "safari", "firefox",
    # 身份 / 人口统计（明确人口学关键名词）
    "名字", "姓名", "称呼", "叫我", "生日", "年龄", "性别", "职业",
    "住址", "居住", "家乡", "籍贯", "老家", "出生地", "祖籍",
    "出生在", "来自", "哪里人", "哪儿人", "国籍", "民族",
    "学历", "学位", "专业", "学校", "毕业", "星座", "生肖",
    # 饮食健康（具体属性词，非泛动词）
    "咖啡", "茶叶", "口味", "忌口", "过敏", "素食", "清真", "冰淇淋", "冰激凌", "雪糕", "甜品", "火锅", "烧烤", "零食",
    # 具体运动与休闲/色彩偏好项目（名词，非泛泛爱好动词）
    "羽毛球", "篮球", "足球", "乒乓", "吉他", "钢琴", "游泳", "读书", "看书", "大海", "蓝色", "红色", "绿色", "黑色", "白色",
    "历史", "唐朝", "宋朝", "明朝", "汉朝", "文史", "古风", "诗词", "国风",
    # 家庭与规划
    "孩子", "子女", "伴侣", "配偶", "婚姻", "职业规划",
    # 语言
    "普通话", "方言", "母语", "英文", "日语",
    # 关系与社交（Phase 19：人际关系路由全局共享）
    "妈妈", "爸爸", "妹妹", "哥哥", "姐姐", "弟弟", "家人",
    "朋友", "同学", "室友", "哥们", "闺蜜", "发小",
    "同事", "领导", "老板", "导师",
}

# 停用词集合：用于 Jaccard 算法计算重叠率时过滤泛词与主谓虚词，防高频泛词污染
STOP_WORDS: set[str] = {
    "我", "你", "他", "她", "它", "们", "的", "是", "在", "有", "个", "也", "很", "把", "让", "给",
    "到", "说", "要", "去", "能", "会", "得", "和", "就", "不", "人", "都", "一", "一个",
    "喜欢", "偏好", "爱", "爱用", "推荐", "习惯", "最爱", "使用", "用", "常用",
    "吃", "喝", "看", "听", "玩", "买", "做", "上", "下", "想", "觉得", "认为",
    "现在", "平时", "平时喜欢", "日常", "经常", "主要", "非常", "特别", "极度",
}


# 城市词库 —— 用于从文本中提取真实地名实体
_CITIES: list[str] = [
    # 直辖市
    "北京", "上海", "天津", "重庆",
    # 省会 & 副省级
    "广州", "深圳", "成都", "杭州", "南京", "武汉", "西安", "长沙", "郑州", "济南",
    "沈阳", "哈尔滨", "长春", "石家庄", "太原", "合肥", "福州", "南昌", "昆明", "贵阳",
    "南宁", "海口", "拉萨", "西宁", "银川", "乌鲁木齐", "呼和浩特", "兰州", "大连", "青岛",
    "宁波", "厦门", "苏州", "无锡", "佛山", "东莞", "温州", "泉州", "烟台", "邯郸",
    "保定", "唐山", "洛阳", "南通", "徐州", "常州", "嘉兴", "绍兴", "金华", "衢州",
    "台州", "丽水", "舟山", "湖州", "义乌", "余杭",
]


def _extract_city_from_text(text: str) -> str:
    """从文本中提取城市名实体，未命中返回空串。"""
    for city in _CITIES:
        if city in text:
            return city
    return ""


# 常见中文姓氏 + 昵称字（用于提取人名实体）
_COMMON_SURNAMES: set[str] = {
    "王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴",
    "徐", "孙", "马", "胡", "朱", "郭", "何", "罗", "高", "林",
    "郑", "梁", "谢", "宋", "唐", "许", "邓", "韩", "冯", "曹",
    "彭", "曾", "肖", "田", "董", "潘", "袁", "蔡", "蒋", "余",
    "于", "杜", "叶", "程", "苏", "魏", "吕", "丁", "任", "沈",
    "姚", "卢", "姜", "崔", "钟", "谭", "陆", "汪", "范", "金",
    "石", "廖", "贾", "夏", "韦", "付", "方", "白", "邹", "孟",
    "熊", "秦", "邱", "江", "尹", "薛", "闫", "段", "雷", "侯",
    "龙", "史", "陶", "黎", "贺", "顾", "毛", "郝", "龚", "邵",
    "万", "钱", "严", "覃", "武", "戴", "莫", "孔", "向", "汤",
}

# 常见中文昵称常用字（小明/小红/阿强等）
_COMMON_NICKNAME_CHARS: set[str] = {
    "明", "红", "强", "伟", "芳", "敏", "静", "丽", "军", "勇",
    "杰", "磊", "洋", "涛", "鹏", "华", "玲", "娟", "秀英", "刚",
}


def _extract_relationship_name(text: str, keyword: str = "") -> str:
    """从文本中提取与关系关键词关联的人名实体。

    策略：
    1. 寻找含姓氏的 2-3 字中文名（如「小明」「张工」「王磊」）
    2. 从「和XX」「跟XX」「XX是」等模式中提取
    3. 返回提取到的人名，未命中返回空串
    """
    import re

    # 策略 1：寻找「姓氏 + 1-2 字」模式（如：张工、小明、王磊）
    # 排除紧跟在姓氏后的功能词（是/的/了/和/跟/与/去/一/起等）
    _NAME_STOP_CHARS = set("是的地得了吗呢啊吧在和跟与去到说就想会能把给让被从对一起有")
    for surname in _COMMON_SURNAMES:
        idx = text.find(surname)
        if idx >= 0 and idx + 1 < len(text):
            remaining = text[idx + 1:]
            m = re.match(r'[\u4e00-\u9fff]{1,2}', remaining)
            if m:
                full_name = surname + m.group(0)
                # 排除非人名词 & 函数词后缀
                _NON_PERSON = {"荣耀", "杭州", "北京", "上海", "咖啡", "外卖", "火锅", "烧烤"}
                if full_name in _NON_PERSON:
                    continue
                # 排除最后一个字是功能词的情况（如 "张工是" → 去掉 "是"）
                if len(full_name) >= 3 and full_name[-1] in _NAME_STOP_CHARS:
                    full_name = full_name[:-1]
                if len(full_name) >= 2:
                    return full_name

    # 策略 2：寻找常见昵称模式（阿X、小X、老X）
    nickname_pattern = re.findall(r'[阿小老][\u4e00-\u9fff]', text)
    if nickname_pattern:
        return nickname_pattern[0]

    # 策略 3：从「和XX一起」「跟XX」中提取相邻词
    _PREFIX_STOP = {"我", "你", "他", "她", "它", "一", "是", "的", "了", "不", "去", "来", "说", "想", "会", "能"}
    _NON_PERSON = {
        "荣耀", "杭州", "北京", "上海", "咖啡", "外卖", "火锅", "烧烤",
        "奶茶", "日料", "西餐", "甜品", "零食", "水果", "海鲜", "茶叶",
        "工作", "项目", "需求", "代码", "文件", "音乐", "电影", "游戏"
    }
    for prefix in ["和", "跟", "与"]:
        m = re.search(rf'{prefix}([\u4e00-\u9fff]{{2,3}})', text)
        if m:
            name = m.group(1)
            # 截断：去掉末尾的功能词（如「李华一」→「李华」）
            while len(name) > 1 and name[-1] in _PREFIX_STOP:
                name = name[:-1]
            if len(name) >= 2 and name not in ("喜欢", "一起", "同事", "朋友", "同学", keyword) and name not in _NON_PERSON:
                return name

    return ""


def _detect_topic(text: str) -> tuple[str, str]:
    """根据确定性规则自动识别文本的 topic (主题) 与 entity (核心实体)。"""
    t_lower = text.lower().strip()

    # 1. 操作系统
    os_kws = ["mac", "windows", "linux", "macos", "ubuntu", "android", "ios", "操作系统"]
    for kw in os_kws:
        if kw in t_lower:
            return "operating_system", kw.title() if kw in ("mac", "windows", "linux", "ubuntu") else kw

    # 2. 硬件设备
    device_map = [
        ("荣耀平板", "hardware_tablet", "荣耀平板"),
        ("ipad", "hardware_tablet", "iPad"),
        ("平板", "hardware_tablet", "平板电脑"),
        ("笔记本", "hardware_pc", "笔记本电脑"),
        ("台式机", "hardware_pc", "台式电脑"),
        ("显示器", "hardware_accessory", "显示器"),
        ("键盘", "hardware_accessory", "键盘"),
        ("鼠标", "hardware_accessory", "鼠标"),
        ("耳机", "hardware_accessory", "耳机"),
        ("电脑", "hardware_pc", "电脑"),
        ("手机", "hardware_mobile", "手机"),
    ]
    for kw, topic, entity in device_map:
        if kw in t_lower:
            return topic, entity

    # 2.5 学习/技能（Phase 19 — 必须放在软件检测之前，"在学Rust" 匹配此处）
    learning_kws = ["在学习", "在学", "正学", "课程", "考证", "自学", "报班"]
    for kw in learning_kws:
        if kw in t_lower:
            return "learning_skill", kw

    # 3. 软件与应用（Phase 16 扩展至 16 项）
    app_map = [
        ("qq", "software_app", "QQ"),
        ("微信", "software_app", "微信"),
        ("vscode", "software_dev", "VSCode"),
        ("cursor", "software_dev", "Cursor"),
        ("chrome", "software_app", "Chrome"),
        ("b站", "software_app", "Bilibili"),
        ("bilibili", "software_app", "Bilibili"),
        ("网易云", "software_app", "网易云音乐"),
        ("spotify", "software_app", "Spotify"),
        ("ide", "software_dev", "IDE"),
        ("figma", "software_dev", "Figma"),
        ("notion", "software_dev", "Notion"),
        ("obsidian", "software_dev", "Obsidian"),
        ("飞书", "software_app", "飞书"),
        ("钉钉", "software_app", "钉钉"),
        ("xmind", "software_dev", "Xmind"),
        ("python", "software_dev", "Python"),
        ("java", "software_dev", "Java"),
        ("golang", "software_dev", "Go"),
        ("rust", "software_dev", "Rust"),
        ("typescript", "software_dev", "TypeScript"),
    ]
    for kw, topic, entity in app_map:
        if kw in t_lower:
            return topic, entity

    # 4. 身份 / 人口统计
    # 优先：城市名直接出现（涵盖「温州人」「来自杭州」「老家温州」等所有变体）
    # 只要文本中含有明确城市名，且上下文带有归属信息词，即识别为 identity_hometown
    city_in_text = _extract_city_from_text(text)
    if city_in_text:
        # 上下文是否含有归属线索词（「X人」后缀 / 老家 / 家乡 / 来自 / 出生 / 籍贯）
        hometown_context_kws = ["人", "老家", "家乡", "籍贯", "出生", "来自", "哪里", "哪儿", "祖籍"]
        if any(kw in text for kw in hometown_context_kws):
            return "identity_hometown", city_in_text

    # 兜底：无城市名但含归属关键词（如「哪里人」「老家在南方」）
    hometown_kws = ["老家", "家乡", "籍贯", "出生地", "哪里人", "哪儿人", "祖籍"]
    for kw in hometown_kws:
        if kw in t_lower:
            return "identity_hometown", city_in_text if city_in_text else kw

    name_kws = ["名字", "叫我", "我叫", "姓名", "称呼"]
    for kw in name_kws:
        if kw in t_lower:
            return "identity_name", kw

    if "生日" in t_lower:
        return "identity_birthday", "生日"
    if "住址" in t_lower or "居住" in t_lower:
        return "identity_location", "住址"
    if "职业" in t_lower or "工作" in t_lower:
        return "identity_job", "职业"

    # ── Phase 19: 以下新增 topic 放在饮食/娱乐之前，避免关键词冲突 ──
    # 例："在学Rust" 应匹配 learning_skill 而非 software_dev
    # 例："和小明吃烧烤" 应先匹配 event_social 而非 dietary_preference（含"烧烤"）

    # 5. 家庭关系（Phase 19 新增）
    family_kws = [
        ("我妈", "relationship_family", "妈妈"), ("我妈妈", "relationship_family", "妈妈"),
        ("我爸", "relationship_family", "爸爸"), ("我爸爸", "relationship_family", "爸爸"),
        ("我妹妹", "relationship_family", "妹妹"), ("我弟弟", "relationship_family", "弟弟"),
        ("我哥哥", "relationship_family", "哥哥"), ("我姐姐", "relationship_family", "姐姐"),
        ("家人", "relationship_family", "家人"), ("家里", "relationship_family", "家人"),
    ]
    for kw, topic, entity in family_kws:
        if kw in t_lower:
            name_entity = _extract_relationship_name(text, kw)
            return topic, name_entity or entity

    # 6. 朋友关系（Phase 19 新增）
    friend_kws = [
        ("朋友", "relationship_friend", "朋友"), ("同学", "relationship_friend", "同学"),
        ("室友", "relationship_friend", "室友"), ("哥们", "relationship_friend", "哥们"),
        ("闺蜜", "relationship_friend", "闺蜜"), ("发小", "relationship_friend", "发小"),
    ]
    for kw, topic, entity in friend_kws:
        if kw in t_lower:
            name_entity = _extract_relationship_name(text, kw)
            return topic, name_entity or entity

    # 7. 同事关系（Phase 19 新增）
    colleague_kws = [
        ("同事", "relationship_colleague", "同事"), ("领导", "relationship_colleague", "领导"),
        ("老板", "relationship_colleague", "老板"), ("导师", "relationship_colleague", "导师"),
    ]
    for kw, topic, entity in colleague_kws:
        if kw in t_lower:
            name_entity = _extract_relationship_name(text, kw)
            return topic, name_entity or entity

    # 8. 健康状态（Phase 19 新增）
    health_kws = [
        "胃疼", "胃痛", "胃不舒服", "头疼", "头痛", "感冒", "发烧", "花粉过敏",
        "失眠", "熬夜", "焦虑", "抑郁", "腰疼", "颈椎",
    ]
    for kw in health_kws:
        if kw in t_lower:
            return "health_condition", kw

    # 9. 工作项目（Phase 19 新增 — 区别于 identity_job）
    work_project_kws = ["项目", "需求", "上线", "重构", "迭代", "排期"]
    for kw in work_project_kws:
        if kw in t_lower:
            return "work_project", kw

    # 11. 娱乐事件（Phase 19 新增 — 放在休闲偏好之前，"看了场电影" 先匹配此处）
    event_entertainment_kws = ["看了场", "去看了", "演唱会", "展览", "博物馆", "逛公园"]
    for kw in event_entertainment_kws:
        if kw in t_lower:
            return "event_entertainment", kw

    # 12. 社交事件（Phase 19 新增 — 放在饮食偏好之前，"和小明吃烧烤" 先匹配此处）
    event_social_kws = ["一起", "聚餐", "聚会", "约会", "见面", "约了"]
    for kw in event_social_kws:
        if kw in t_lower:
            name_entity = _extract_relationship_name(text, kw)
            return "event_social", name_entity or kw
    # 人名 + 饮食上下文 = 社交事件（如「和小明吃烧烤」无「一起」关键词）
    _FOOD_CONTEXT = {"烧烤", "火锅", "日料", "西餐", "甜品", "海鲜", "外卖", "吃了", "吃", "喝", "饭"}
    if any(fw in t_lower for fw in _FOOD_CONTEXT):
        name_entity = _extract_relationship_name(text)
        if name_entity:
            return "event_social", name_entity

    # 13. 出行事件（Phase 19 新增）
    travel_kws = ["出差", "旅行", "旅游", "高铁", "火车", "飞机"]
    for kw in travel_kws:
        if kw in t_lower:
            return "event_travel", kw

    # 14. 生活里程碑事件（Phase 19 新增）
    life_kws = ["搬家", "换工作", "买房", "买车", "结婚", "毕业"]
    for kw in life_kws:
        if kw in t_lower:
            return "event_life", kw

    # 15. 日常琐事（Phase 19 新增）
    event_daily_kws = ["去了趟", "逛了", "超市", "买菜", "快递"]
    for kw in event_daily_kws:
        if kw in t_lower:
            return "event_daily", kw

    # 16. 饮食偏好（Phase 16 扩展至 27 项，含「辣」）
    food_kws = [
        "咖啡", "茶叶", "茶", "奶茶", "外卖", "火锅", "过敏", "巧克力", "可乐", "雪碧",
        "烧烤", "日料", "西餐", "甜品", "素食", "海鲜", "牛奶", "花生", "麸质", "忌口",
        "烘焙", "早餐", "夜宵", "零食", "方便面", "螺蛳粉", "辣",
    ]
    for kw in food_kws:
        if kw in t_lower:
            return "dietary_preference", kw

    # 17. 休闲与运动（Phase 16 扩展至 22 项）
    entertainment_kws = [
        "羽毛球", "篮球", "足球", "游泳", "跑步", "健身", "游戏", "许嵩", "周杰伦", "音乐", "电影", "小说",
        "原神", "崩坏", "魔兽", "cs", "lol", "王者", "吃鸡", "番剧", "直播", "桌游", "剧本杀", "密室",
        "历史", "唐朝", "宋朝", "明朝", "汉朝", "文史", "古风", "诗词", "国风",
    ]
    for kw in entertainment_kws:
        if kw in t_lower:
            return "entertainment_hobby", kw

    return "general_preference", ""


SINGLE_VALUED_TOPICS = {
    "operating_system",
    "hardware_pc",
    "hardware_tablet",
    "hardware_mobile",
    "identity_name",
    "identity_hometown",
    "identity_location",
    "identity_job",
    "identity_birthday",
    "behavior_profile",  # Phase 19: 行为画像卡只有一个
}

# ── Phase 13: 六维度遗忘体系 ─────────────────────────────

# 旧 topic 名 → 新 topic 名映射（向后兼容旧代码留下的数据）
_TOPIC_ALIASES = {
    "location_hometown": "identity_hometown",
    "location": "identity_location",
}


def _normalize_topic(topic: str | None) -> str:
    """将旧 topic 名标准化为当前命名（如 location_hometown → identity_hometown）。"""
    if not topic:
        return "general_preference"
    return _TOPIC_ALIASES.get(topic, topic)


# 维度一：差异化周衰减率（topic → decay_factor，每 7 天乘一次）
TOPIC_DECAY_RATES: dict[str, float] = {
    # ── 身份类（几乎不变，每周衰减 1-1.5%）──
    "identity_name": 0.99,
    "identity_hometown": 0.99,
    "identity_birthday": 0.99,
    "identity_location": 0.985,
    "identity_job": 0.985,
    # ── 设备与系统（每周衰减 1.5-2%）──
    "operating_system": 0.985,
    "hardware_pc": 0.985,
    "hardware_tablet": 0.98,
    "hardware_mobile": 0.98,
    "hardware_accessory": 0.94,
    # ── 软件/偏好/技能类（每周衰减 7-8%）──
    "software_dev": 0.94,
    "software_app": 0.93,
    "dietary_preference": 0.94,
    "entertainment_hobby": 0.93,
    "general_preference": 0.93,
    "learning_skill": 0.92,
    "work_project": 0.90,
    "health_condition": 0.90,
    # ── 关系类（每周衰减 4-8%）──
    "relationship_family": 0.97,
    "relationship_friend": 0.94,
    "relationship_colleague": 0.92,
    # ── 事件类（敏捷衰减·每周 15-20%）──
    "event_social": 0.85,
    "event_daily": 0.80,
    "event_entertainment": 0.82,
    "event_travel": 0.85,
    "event_life": 0.90,
    # 行为画像卡（永久不过期）
    "behavior_profile": 1.0,
}
DEFAULT_DECAY_RATE = 0.90  # 未知 topic 的默认周衰减率（每周 10%）

# 维度二：差异化衰减阈值（topic → 低于此值不注入 Prompt）
TOPIC_DECAY_THRESHOLDS: dict[str, float] = {
    # identity_* → 0.10（身份信息即使极低置信也不消失）
    "identity_name": 0.10,
    "identity_hometown": 0.10,
    "identity_birthday": 0.10,
    "identity_location": 0.10,
    "identity_job": 0.10,
    # 主力设备 → 0.30
    "operating_system": 0.30,
    "hardware_pc": 0.30,
    "hardware_tablet": 0.30,
    "hardware_mobile": 0.30,
    # 外设换得快 → 0.40（更早退出注入）
    "hardware_accessory": 0.40,
    # 标准 → 0.35
    "software_dev": 0.35,
    "software_app": 0.35,
    "dietary_preference": 0.35,
    "entertainment_hobby": 0.35,
    "general_preference": 0.35,
    # Phase 19：关系类（很低阈值 → 几乎永久保留）
    "relationship_family": 0.15,
    "relationship_friend": 0.25,
    "relationship_colleague": 0.28,
    # Phase 19：长期追踪类
    "learning_skill": 0.30,
    "work_project": 0.35,
    "health_condition": 0.30,
    # Phase 19：事件类（激进高阈值 → 早早退出 Prompt）
    "event_social": 0.50,
    "event_daily": 0.50,
    "event_entertainment": 0.48,
    "event_travel": 0.40,
    "event_life": 0.38,
    # Phase 19：行为画像卡（永不因衰减而退出）
    "behavior_profile": 0.0,
}
DEFAULT_DECAY_THRESHOLD = 0.35

# 维度四：会触发同 Topic 竞争衰减的 topic 集合
COMPETITIVE_DECAY_TOPICS = {
    "hardware_accessory",
    "hardware_mobile",
    "hardware_tablet",
    "software_app",
    "software_dev",
    "entertainment_hobby",
}


# Topic 差异化合并策略（Phase 15 + Phase 19 扩展）
TOPIC_MERGE_STRATEGIES: dict[str, str] = {
    # single: 整个 topic 只保留一条当前值，始终 UPDATE
    "identity_name": "single",
    "identity_hometown": "single",
    "identity_location": "single",
    "identity_job": "single",
    "identity_birthday": "single",
    "operating_system": "single",
    "hardware_pc": "single",
    "hardware_tablet": "single",
    "hardware_mobile": "single",
    # entity_replace: 同 entity 覆盖更新，异 entity 累积并存
    "hardware_accessory": "entity_replace",
    "relationship_friend": "entity_replace",
    "relationship_family": "entity_replace",
    "relationship_colleague": "entity_replace",
    # multi: 始终 ADD 独立保存，多偏好/事件共存
    "dietary_preference": "multi",
    "entertainment_hobby": "multi",
    "software_dev": "multi",
    "software_app": "multi",
    "general_preference": "multi",
    "event_social": "multi",
    "event_daily": "multi",
    "event_entertainment": "multi",
    "event_travel": "multi",
    "event_life": "multi",
    "health_condition": "multi",
    "learning_skill": "multi",
    "work_project": "multi",
    # Phase 19：行为画像卡 — 始终 UPDATE，永久保留
    "behavior_profile": "single",
}

# Phase 19：可压缩 topic 集合（慢衰减、非事件、非 single 的 topic）
# single 类只有一个条目无需压缩；事件类靠激进遗忘自清理
COMPRESSIBLE_TOPICS: set[str] = {
    "hardware_accessory",
    "software_dev", "software_app",
    "dietary_preference", "entertainment_hobby",
    "general_preference",
    "relationship_friend", "relationship_family", "relationship_colleague",
    "learning_skill", "work_project",
}
# 压缩触发阈值：同 topic 下碎片数 ≥ 此值才压缩
COMPRESS_MIN_FRAGMENTS = 8
# 压缩触发间隔：每 N 轮抽取后检查一次
COMPRESS_CHECK_INTERVAL = 30

# Phase 19：事件记忆定期清理（防止 DB 膨胀）
EVENT_PURGE_DAYS = 90         # 只清理创建超过 N 天的事件记忆
EVENT_PURGE_CONFIDENCE = 0.25  # 且原始置信度已低于此值（深度衰减后）
EVENT_PURGE_INTERVAL = 100     # 每 N 轮检查一次

# ── Phase 26: 游戏 Lore 记忆泄漏防护 ──
# 注入到 system prompt 的崩铁世界观知识，不应被提取为"用户记忆"写入 DB。
# 至少命中 2 个关键词才判定为泄漏并丢弃。
_LORE_LEAK_KEYWORDS: set[str] = {
    # 星神
    "星神", "命途", "阿基维利", "纳努克", "岚", "药师", "克里珀", "希佩",
    "浮黎", "博识尊", "反物质军团",
    # 星球/区域
    "仙舟", "罗浮", "朱明", "曜青", "匹诺康尼", "雅利洛", "贝洛伯格",
    "黑塔空间站", "翁法罗斯", "格拉默", "联觉梦境", "家族",
    # 势力
    "星核猎手", "星穹列车", "星际和平公司", "天才俱乐部",
    "博识学会", "流光忆庭", "忆庭", "焚化工",
    # 角色名
    "银狼", "卡芙卡", "刃", "艾利欧", "星期日", "知更鸟", "花火",
    "黄泉", "翡翠", "加拉赫", "黑天鹅", "布洛妮娅", "希儿",
    "景元", "丹恒", "三月七", "姬子", "瓦尔特", "符玄",
    "开拓者", "砂金", "托帕", "镜流", "罗刹", "彦卿",
    # 概念
    "模拟宇宙", "差分宇宙", "星核", "裂界", "虚数", "忆域迷因",
    "谐乐大典", "热砂盛典", "寰宇蝗灾", "云上五骁",
    # 流萤相关
    "失熵症", "铁骑", "格拉默铁骑",
    # 翁法罗斯补充 (v0.2.0-alpha.29)
    "那刻夏", "盗火行者", "再创世", "赛飞儿", "刻律德菈",
    "缇宁", "缇安", "德谬歌", "丹枫", "半神议院", "逐火之旅",
    # 二相乐园补充 (v0.2.0-alpha.29)
    "不死神探事务所", "乔瓦尼", "啵啵娃", "归寂", "血涂游戏", "幸福手术",
    # 模板特征词
    "实装版本", "角色故事", "语音内容", "出场人物", "系列任务",
}


def _is_lore_leak(content: str) -> bool:
    """检测内容是否为游戏 lore 泄漏（不应写入用户记忆）。

    Returns True 表示应丢弃此记忆。
    """
    hit_count = sum(1 for kw in _LORE_LEAK_KEYWORDS if kw in content)
    return hit_count >= 2  # 至少命中 2 个关键词才判定为泄漏


def _build_compression_prompt(topic: str, memories: list[dict]) -> str:
    """构建同 topic 碎片记忆归纳的 LLM prompt。"""
    memory_lines = "\n".join(
        f"- {m['content']}（置信度 {m.get('confidence', 0):.2f}）"
        for m in memories
    )
    return f"""你是一个记忆归纳助手。请将以下同一主题（{topic}）的多条碎片记忆归纳为一条精炼摘要。

## 归纳规则
1. 保留所有非重复的重要信息，去除冗余
2. 精炼为 1-2 句中文，≤ 100 字
3. 若碎片中体现明显偏好模式（如多次出现的食物/工具/爱好），总结该模式
4. 对矛盾信息，取置信度更高或内容更新的一方
5. 以第三人称视角（"用户"开头）
6. 只输出归纳后的纯文本，无需 JSON 或任何标记

## 碎片记忆
{memory_lines}

## 归纳结果（仅输出文本）"""

# Phase 19：语义冲突检测配置
CONFLICT_CHECK_INTERVAL = 10  # 每 N 条新记忆写入后触发一次
CONFLICT_SCAN_LIMIT = 20      # 每次扫描同 namespace+type 的最近 N 条


def _build_conflict_detection_prompt(new_memory: str, recent_memories: list[dict]) -> str:
    """构建语义冲突检测的 LLM prompt。"""
    recent_str = "\n".join(
        f"- ID={m['id'][:16]} | {m['content']}（conf={m.get('confidence', 0):.2f}）"
        for m in recent_memories
    )
    return f"""你是一个记忆一致性检查助手。请判断以下新记忆与已有记忆之间是否存在语义矛盾。

## 新记忆
"{new_memory}"

## 已有记忆（最近 20 条同类型）
{recent_str}

## 判定规则
分析新记忆与已有记忆的关系，返回 JSON：
- "preference_upgrade": 新记忆表明偏好升级/替换了旧记忆（如从喜欢A变为喜欢B）
  → 返回 {{"type":"preference_upgrade","target_id":"旧记忆ID"}}
- "state_change": 新记忆表明状态发生了变化（如以前健康现在生病），两者都成立只是时间不同
  → 返回 {{"type":"state_change"}}
- "contradiction": 新记忆与某条旧记忆在同一时间维度上逻辑矛盾
  → 返回 {{"type":"contradiction","target_id":"旧记忆ID"}}
- "no_conflict": 没有矛盾，新记忆是独立的新信息
  → 返回 {{"type":"no_conflict"}}

## 输出（严格 JSON，无其他内容）
"""

# Phase 19：行为画像卡 prompt
PROFILE_UPDATE_INTERVAL = 40  # 每 40 轮更新一次行为画像


def _build_profile_prompt(recent_memories: list[dict], existing_profile: str = "") -> str:
    """构建行为画像卡更新 prompt。"""
    memory_lines = "\n".join(
        f"- [{m.get('topic', '?')}] {m['content']}"
        for m in recent_memories[:30]
    )
    profile_section = f"## 当前画像\n{existing_profile}\n" if existing_profile else ""
    return f"""你是一个用户行为分析助手。请根据用户的长期记忆，提炼出一个结构化的行为画像 JSON。

## 画像维度
- work_style: 工作风格和习惯（如"习惯早起工作"、"喜欢先写测试"）
- communication: 沟通偏好（如"说话直接"、"喜欢短句"、"不爱客套"）
- current_focus: 当前关注点（如"最近在学习 Rust"、"正在做用户系统重构"）
- decision_style: 决策风格（如"偏好数据驱动"、"会先调研再动手"）
- emotional_trend: 近期情绪趋势（如"最近一周情绪较好"、"工作热情高"）
- habits: 值得注意的习惯或模式

## 规则
1. 只输出 JSON，不要任何其他文本
2. 如果某维度没有足够信息，值设为 null
3. 每个维度用简洁的 1 句中文描述，≤ 20 字
4. 根据记忆内容推断而非凭空猜测

{profile_section}## 最近的长期记忆（取样 30 条）
{memory_lines}

## 输出（严格 JSON）"""


def _detect_entity(text: str, topic: str = "") -> str:
    """从文本中提取品牌+型号作为 entity 标识符。

    用于 entity_replace 策略的 entity 匹配（如"罗技 G502" → "logitech_g502"）。
    """
    import re
    t_lower = text.lower()

    # 品牌识别映射（品牌别名归一化）
    brand_patterns: list[tuple[list[str], str]] = [
        (["罗技", "logitech"], "logitech"),
        (["雷蛇", "razer"], "razer"),
        (["海盗船", "corsair"], "corsair"),
        (["赛睿", "steelseries"], "steelseries"),
        (["迈从"], "maicong"),
        (["达尔优", "dareu"], "dareu"),
        (["苹果", "apple"], "apple"),
        (["华为", "huawei"], "huawei"),
        (["三星", "samsung"], "samsung"),
        (["小米", "xiaomi"], "xiaomi"),
        (["戴尔", "dell"], "dell"),
        (["联想", "lenovo"], "lenovo"),
        (["华硕", "asus"], "asus"),
        (["惠普", "hp"], "hp"),
        (["thinkpad"], "thinkpad"),
        (["荣耀", "honor"], "honor"),
    ]

    detected_brand = ""
    for aliases, normalized in brand_patterns:
        if any(a in t_lower for a in aliases):
            detected_brand = normalized
            break

    if not detected_brand:
        # 无品牌匹配，返回前 3 个词的简化
        words = text.split()[:3]
        return "_".join(w.lower() for w in words) if words else "unknown"

    # 型号提取：品牌名之后紧跟的型号组合（如 "G502"、"MX Master 3"）
    model = ""
    for aliases, _ in brand_patterns:
        for alias in aliases:
            idx = t_lower.find(alias)
            if idx >= 0:
                after = text[idx + len(alias):].strip()
                m = re.search(r'[\w]+(?:\s+[\w]+){0,2}', after)
                if m:
                    model = m.group(0).strip().lower().replace(" ", "_")
                break
        if model:
            break

    return f"{detected_brand}_{model}" if model else detected_brand


class PersonalMemoryManager:
    """个人长期记忆管理器。"""

    def __init__(self):
        self.settings = get_settings()
        self._short_term: list[dict] = []
        self._message_count: int = 0  # 记忆抽取间隔计数
        self._extracting: bool = False  # 防止并发抽取导致重复记忆
        self._pending_context: Optional[tuple] = None  # 当抽取中时，暂存最新的并发抽取请求，任务结束后自动排队执行
        self._compress_counter: int = 0  # Phase 19: 压缩间隔计数
        self._compressing: bool = False  # Phase 19: 防止并发压缩
        self._conflict_counter: int = 0  # Phase 19: 冲突检测间隔计数
        self._detecting_conflicts: bool = False  # Phase 19: 防止并发冲突检测
        self._profile_counter: int = 0  # Phase 19: 行为画像更新间隔计数
        self._purge_counter: int = 0  # Phase 19: 事件记忆清理间隔计数

    # ── 短期缓冲（内存级滑动窗口）─────────────────────────────

    def add_message(self, role: str, content: str, counting_for_extract: bool = True):
        """添加一条消息到短期缓冲，保留 short_term_window 条。

        counting_for_extract=False 用于会话加载等回放场景：
        历史消息只入缓冲，不计入抽取间隔，避免首轮就误触发抽取。
        """
        self._short_term.append({"role": role, "content": content})
        window = self.settings.memory.short_term_window
        if len(self._short_term) > window:
            self._short_term = self._short_term[-window:]
        if counting_for_extract:
            self._message_count += 1

    def get_short_term(self) -> list[dict]:
        return list(self._short_term)

    def get_short_term_as_llm(self) -> list[LLMMessage]:
        return [LLMMessage(role=m["role"], content=m["content"]) for m in self._short_term]

    def clear_short_term(self):
        """清空短期内存缓冲（保留消息总计数以支持后台提取调度）。"""
        self._short_term.clear()

    # ── 命名空间隔离 ─────────────────────────────────────────

    def get_namespaces(self, mode: str) -> list[str]:
        """根据日常(daily)/工作(work)模式获取包含的命名空间。"""
        if not mode or mode == "all":
            return ["shared_profile", "daily_life", "work_tasks"]
        namespaces = ["shared_profile"]
        if mode == "daily":
            namespaces.append("daily_life")
        else:
            namespaces.append("work_tasks")
        return namespaces

    def switch_namespace(self, new_mode: str):
        """切换模式时清空短期缓冲（长期记忆依靠 namespace 隔离）。"""
        self.clear_short_term()

    # ── 长期个人记忆检索与写入 ───────────────────────────────

    async def recall(
        self,
        query: str,
        mode: str = "daily",
        top_k: int = 5,
        min_similarity: Optional[float] = None,
    ) -> list[dict]:
        """从 SQLite memories 表通过向量余弦相似度检索个人长期记忆。

        相似度阈值按当前 Embedding 引擎自动选择：
        - 哈希引擎 (LocalEmbeddingEngine): 默认 0.10（低精度，依赖关键词）
        - ONNX 引擎 (OnnxEmbeddingEngine): 默认 0.35（真语义余弦，高可解释性）

        Args:
            min_similarity: 若显式传入则使用传入值；None 时自动选择引擎默认值。
        """
        import time as _time

        # 自动选择当前引擎对应的默认相似度阈值
        if min_similarity is None:
            engine = get_embedding_engine()
            if isinstance(engine, OnnxEmbeddingEngine):
                min_similarity = 0.35  # ONNX 真余弦
            else:
                min_similarity = 0.10  # 哈希引擎

        namespaces = self.get_namespaces(mode)
        all_memories: list[dict] = []
        for ns in namespaces:
            all_memories.extend(_db.query_memories(ns, min_confidence=0.0))

        if not all_memories:
            return []

        # ── Phase 13: 按 Topic 差异化时间衰减 ──
        now_ms = int(_time.time() * 1000)
        MS_PER_DAY = 86400_000

        for m in all_memories:
            raw_conf = m.get("confidence", 0.0)
            raw_topic = m.get("topic")
            if not raw_topic:
                raw_topic, _ = _detect_topic(m.get("content", ""))
            topic = _normalize_topic(raw_topic)
            last_ts = m.get("last_accessed_at")

            # 维度一：按 topic 取差异化衰减率
            decay_rate = TOPIC_DECAY_RATES.get(topic, DEFAULT_DECAY_RATE)

            if last_ts and last_ts > 0:
                days_since = max(0, (now_ms - last_ts) / MS_PER_DAY)
                decay = decay_rate ** (days_since / 7.0)  # 周级 7 天基准衰减
                effective_conf = raw_conf * decay
                # 将敏捷时间衰减持久化落盘至 SQLite 数据库（门槛 0.01）
                if raw_conf - effective_conf > 0.01:
                    new_conf = round(effective_conf, 4)
                    _db.update_memory(m["id"], confidence=new_conf)
                    m["confidence"] = new_conf
                    raw_conf = new_conf
            else:
                decay = 1.0
                effective_conf = raw_conf
            m["_effective_confidence"] = effective_conf
            m["_decay"] = decay
            m["_days_since_access"] = (now_ms - last_ts) / MS_PER_DAY if last_ts and last_ts > 0 else 0
            m["_topic"] = topic

        # 维度二：按 topic 取差异化衰减阈值过滤（同时遵循全局 confidence_threshold 最低门槛）
        min_conf_gate = getattr(self.settings.memory, "confidence_threshold", 0.0)
        all_memories = [
            m for m in all_memories
            if m["_effective_confidence"] >= max(
                min_conf_gate,
                TOPIC_DECAY_THRESHOLDS.get(
                    _normalize_topic(m.get("topic")), DEFAULT_DECAY_THRESHOLD
                )
            )
        ]

        if not query or not query.strip():
            # 无 query 时按有效置信度 + 最近更新时间降序
            all_memories.sort(key=lambda m: (m["_effective_confidence"], m.get("updatedAt", 0)), reverse=True)
            top = all_memories[:top_k]
            for m in top:
                _db.touch_memory(m["id"])
            return top

        # 1. 计算 Query 的本地 Embedding 向量
        q_vec = get_embedding_engine().embed_text(query)
        keywords = _tokenize_query(query)

        # Phase 25：双引擎混合召回 — 查询哈希领域向量（23 类范畴增强）
        _use_hybrid = isinstance(get_embedding_engine(), OnnxEmbeddingEngine)
        if _use_hybrid:
            _hash_eng = get_hash_engine()
            q_hash_vec = _hash_eng.embed_text(query)
            # 预计算所有记忆的哈希向量（以 content 为 key 缓存去重）
            _hash_cache: dict[str, np.ndarray] = {}
            for m in all_memories:
                c = m.get("content", "")
                if c not in _hash_cache:
                    _hash_cache[c] = _hash_eng.embed_text(c)

        # Phase 19: 提取查询中的人名用于 relationship 记忆 boost
        _query_names = _extract_relationship_name(query) if query and query.strip() else ""

        # 2. 对所有记忆计算向量余弦相似度得分
        scored_memories: list[tuple[float, dict]] = []
        for m in all_memories:
            emb_blob = m.get("embedding")
            vec = blob_to_vector(emb_blob)
            
            if vec is not None:
                sim_score = cosine_similarity(q_vec, vec)
            else:
                # 兼容无向量的历史旧记忆（关键词降级匹配）
                content_lower = m["content"].lower()
                sim_score = 0.5 if any(kw in content_lower for kw in keywords) else 0.0

            # Phase 25：双引擎混合 — ONNX 语义 + 哈希领域增强
            if _use_hybrid:
                content_text = m.get("content", "")
                h_vec = _hash_cache.get(content_text)
                if h_vec is not None:
                    hash_sim = cosine_similarity(q_hash_vec, h_vec)
                    # 领域相似度限幅：仅在 0.3~0.9 区间有效（防止噪声）
                    hash_sim = max(0.0, min(0.9, hash_sim))
                else:
                    hash_sim = 0.0
                # ONNX 主导 (75%) + 哈希增强 (25%)
                sim_score = sim_score * 0.75 + hash_sim * 0.25

            # 结合字符重叠微量增益与置信度微调得分
            content_lower = m["content"].lower()
            kw_overlap = sum(1 for kw in keywords if kw in content_lower)
            kw_boost = min(0.2, kw_overlap * 0.08)

            semantic_match_score = sim_score + kw_boost
            # 用时间衰减后的有效置信度参与排序（时效旧记忆排名自动下降）
            final_score = semantic_match_score * 0.8 + (m.get("_effective_confidence", 0.8) * 0.2)

            # 3. 余弦相似度门限防护：仅保留纯语义匹配分高于 min_similarity 的条目
            if semantic_match_score >= min_similarity:
                scored_memories.append((final_score, m))

        # 4. 按最终相关度 + 新设备优先加权 + 人物名 boost 排序
        # 维度六：同 Topic 内距上次访问 < 30 天的记忆 score × 1.5
        # Phase 19 人物触发：查询中包含人名 → 匹配 relationship_* 记忆 entity  → score × 2.0
        def _rank_key(item: tuple[float, dict]) -> float:
            score, m = item
            days = m.get("_days_since_access", 999)
            if days < 30:
                score *= 1.5
            # 人物名触发：查询提到的人名匹配 relationship 记忆的 entity
            if _query_names and m.get("topic", "").startswith("relationship_"):
                entity = m.get("entity", "").strip()
                if entity and _query_names in entity:
                    score *= 2.0
            return score

        scored_memories.sort(key=_rank_key, reverse=True)

        results = [item[1] for item in scored_memories[:top_k]]

        # ── Topic 感知降级召回：向量搜索失效时按查询意图直接取 topic ──
        # 解决哈希引擎无法语义匹配「家乡在哪」↔「是杭州人」的问题。
        # 当向量检索结果不足时，用 _detect_topic 推断用户意图并回退拉取同 topic 记忆。
        if len(results) < top_k and query.strip():
            query_topic, _ = _detect_topic(query)
            if query_topic and query_topic != "general_preference":
                result_ids = {m.get("id") for m in results}
                # 从已加载的记忆池中按 topic 匹配（归一化后比较），按置信度降序
                topic_candidates = sorted(
                    [m for m in all_memories
                     if _normalize_topic(m.get("topic")) == query_topic
                     and m.get("id") not in result_ids
                     and m.get("_effective_confidence", 0) >= min_similarity],
                    key=lambda m: (m.get("_effective_confidence", 0), m.get("confidence", 0)),
                    reverse=True,
                )
                for m in topic_candidates:
                    if len(results) >= top_k:
                        break
                    results.append(m)
                    _db.touch_memory(m["id"])

        # ── Phase 26: 近期记忆旁路 — 哈希引擎跨话题相似度接近 0 的补偿 ──
        # 相似度过滤会把「大海」和「松鼠」这类语义无关但同属用户偏好的记忆全部滤掉。
        # 这里额外补充 7 天内访问过的记忆，保证 AI 始终知晓近期对话内容。
        if query.strip() and len(results) < top_k:
            result_ids = {m.get("id") for m in results}
            recent_candidates = sorted(
                [m for m in all_memories
                 if m.get("id") not in result_ids
                 and m.get("_days_since_access", 999) < 7
                 and m.get("_effective_confidence", 0) >= 0.3],
                key=lambda m: (m.get("_days_since_access", 999), -(m.get("confidence", 0))),
            )
            for m in recent_candidates:
                if len(results) >= top_k:
                    break
                results.append(m)

        # 维度五：被召回的记忆刷新 last_accessed_at + 微量 confidence 增益
        for m in results:
            _db.touch_memory(m["id"], confidence_boost=0.03)
        return results

    async def write_long_term(
        self,
        content: str,
        metadata: dict,
        confidence: float,
        namespace: str = "shared_profile",
        topic: Optional[str] = None,
        entity: Optional[str] = None,
    ) -> bool:
        """根据置信度过滤与本地向量计算写入长期个人记忆。
        门槛统一读取 config 中的 confidence_threshold（默认 0.65）。"""
        if confidence < self.settings.memory.confidence_threshold:
            return False

        # Phase 26: 游戏 lore 泄漏拦截
        if _is_lore_leak(content):
            logger.warning("[memory] lore 泄漏拦截: '%s...'", content[:60])
            return False

        mem_id = f"mem-{uuid.uuid4().hex[:12]}"
        mem_type = metadata.get("type", "user_profile")

        if not topic or not entity:
            det_topic, det_entity = _detect_topic(content)
            topic = topic or det_topic
            entity = entity or det_entity
        
        # 本地计算 Embedding 向量并转为 BLOB 存储
        vec = get_embedding_engine().embed_text(content)
        emb_bytes = vector_to_blob(vec)

        _db.save_memory(
            memory_id=mem_id,
            mem_type=mem_type,
            content=content,
            namespace=namespace,
            confidence=confidence,
            embedding=emb_bytes,
            topic=topic,
            entity=entity,
        )
        return True

    async def update_long_term(
        self,
        memory_id: str,
        content: str,
        confidence: float = 0.9,
        namespace: Optional[str] = None,
        topic: Optional[str] = None,
        entity: Optional[str] = None,
    ) -> bool:
        """更新已知记忆的内容与本地向量。"""
        vec = get_embedding_engine().embed_text(content)
        emb_bytes = vector_to_blob(vec)
        if not topic or not entity:
            det_topic, det_entity = _detect_topic(content)
            topic = topic or det_topic
            entity = entity or det_entity
        return _db.update_memory(
            memory_id=memory_id,
            content=content,
            confidence=confidence,
            namespace=namespace,
            embedding=emb_bytes,
            topic=topic,
            entity=entity,
        )

    async def delete_long_term(self, memory_id: str) -> bool:
        """从 SQLite 删除指定的长期记忆条目。"""
        return _db.delete_memory(memory_id)

    async def _apply_competitive_decay(self, topic: str, exclude_entity: str = ""):
        """维度四：同 Topic 竞争衰减 —— 对同 topic 旧记忆执行一次 0.85x confidence 乘算。

        仅在 topic 属于 COMPETITIVE_DECAY_TOPICS 时生效。
        用于 entity_replace 策略下新 entity 取代旧 entity 时降低旧记忆权重。
        """
        if topic not in COMPETITIVE_DECAY_TOPICS:
            return
        all_ns = ["shared_profile", "daily_life", "work_tasks"]
        for ns in all_ns:
            existing = _db.query_memories(ns, min_confidence=0.0)
            for m in existing:
                if m.get("topic") != topic:
                    continue
                if exclude_entity and m.get("entity", "").lower().strip() == exclude_entity.lower().strip():
                    continue  # 不衰减同 entity 的新记忆
                new_conf = max(0.10, (m.get("confidence", 0.5) * 0.85))
                _db.update_memory(m["id"], confidence=new_conf)

    async def consolidate_memory(
        self,
        provider,
        content_text: str,
        mem_type: str = "user_profile",
        confidence: float = 0.9,
        namespace: str = "shared_profile",
        topic: Optional[str] = None,
        entity: Optional[str] = None,
    ) -> str:
        """Mem0 风格的主题隔离记忆整合链 (ADD/UPDATE/DELETE/IGNORE)。

        Phase 15 升级：按 TOPIC_MERGE_STRATEGIES 执行三种差异化合并策略：
        - single:   整个 topic 仅保留一条（身份/主力设备），始终 UPDATE
        - entity_replace: 同 entity 覆盖，异 entity 累积并存（外设）
        - multi:    始终 ADD 独立保存，多偏好共存（饮食/娱乐/软件）

        Returns:
            "ADD" | "UPDATE" | "DELETE" | "IGNORE"
        """
        if confidence < self.settings.memory.confidence_threshold:
            logger.info("[memory] consolidate 忽略低置信度候选: conf=%.2f < threshold=%.2f ('%s...')",
                        confidence, self.settings.memory.confidence_threshold, content_text[:40])
            return "IGNORE"

        # Phase 26: 游戏 lore 泄漏拦截
        if _is_lore_leak(content_text):
            logger.warning("[memory] consolidate 拦截 lore 泄漏: '%s...'", content_text[:60])
            return "IGNORE"

        if not topic or not entity:
            det_topic, det_entity = _detect_topic(content_text)
            topic = topic or det_topic
            entity = entity or det_entity

        # 如果 topic 仍是兜底值，尝试通过品牌识别补充 entity
        if not entity or entity == topic:
            detected = _detect_entity(content_text, topic)
            if detected and detected != "unknown":
                entity = detected

        # ── Phase 15: 按合并策略分流 ──
        merge_strategy = TOPIC_MERGE_STRATEGIES.get(_normalize_topic(topic), "multi")

        if merge_strategy == "multi":
            # 多偏好共存：语义去重 + 完全同文本去重
            existing = _db.query_memories(namespace, min_confidence=0.0)
            for m in existing:
                if m.get("content", "").strip().lower() == content_text.strip().lower():
                    return "IGNORE"
            # Phase 25: 无精确匹配时，用 embedding 相似度做语义去重
            q_vec = get_embedding_engine().embed_text(content_text)
            for m in existing:
                emb = m.get("embedding")
                if emb is not None:
                    vec = blob_to_vector(emb)
                    if vec is not None:
                        sim = cosine_similarity(q_vec, vec)
                        if sim > 0.88:
                            # 语义高度重复 → IGNORE，合并置信度到旧记忆
                            new_conf = max(m.get("confidence", 0.7), confidence * 0.95)
                            await self.update_long_term(
                                m["id"], m["content"], new_conf, namespace, topic, entity,
                            )
                            logger.debug("[memory] multi 语义去重: sim=%.3f '%s' ≈ '%s'", sim,
                                         content_text[:30], m.get("content", "")[:30])
                            return "IGNORE"
            await self.write_long_term(content_text, {"type": mem_type}, confidence, namespace, topic, entity)
            return "ADD"

        # 查找同 topic 的已有记忆（single 和 entity_replace 都需要）
        all_namespaces = ["shared_profile", "daily_life", "work_tasks"]
        all_existing: list[dict] = []
        for ns in all_namespaces:
            mem_list = _db.query_memories(ns, min_confidence=0.0)
            for m in mem_list:
                if not m.get("topic"):
                    t, e = _detect_topic(m.get("content", ""))
                    m["topic"] = t
                    m["entity"] = e
                    _db.update_memory(m["id"], topic=t, entity=e)
            all_existing.extend(mem_list)

        topic_matched = [m for m in all_existing if m.get("topic") == topic]

        if merge_strategy == "single":
            # 同 topic 内只保留一条：有旧记录则 UPDATE，否则 ADD
            if topic_matched:
                existing = topic_matched[0]
                # 若内容完全相同 → IGNORE
                if existing["content"].strip().lower() == content_text.strip().lower():
                    return "IGNORE"
                await self.update_long_term(existing["id"], content_text, confidence, namespace, topic, entity)
                return "UPDATE"
            await self.write_long_term(content_text, {"type": mem_type}, confidence, namespace, topic, entity)
            return "ADD"

        # ── entity_replace 策略：同 entity 覆盖，异 entity 累积 ──
        # 查找同 entity 的已有记忆
        same_entity = [m for m in topic_matched
                       if m.get("entity", "").lower().strip() == entity.lower().strip()]

        if same_entity:
            existing = same_entity[0]
            if existing["content"].strip().lower() == content_text.strip().lower():
                return "IGNORE"
            await self.update_long_term(existing["id"], content_text, confidence, namespace, topic, entity)
            return "UPDATE"

        # 异 entity → 先做 embedding 预筛，高相似度直接 UPDATE 绕过 LLM 判决
        q_vec = get_embedding_engine().embed_text(content_text)
        scored_existing: list[tuple[float, dict]] = []
        for m in topic_matched:
            emb_blob = m.get("embedding")
            vec = blob_to_vector(emb_blob)
            if vec is not None:
                sim = cosine_similarity(q_vec, vec)
                if sim >= 0.10:
                    scored_existing.append((sim, m))

        scored_existing.sort(key=lambda x: x[0], reverse=True)

        # Phase 25: 异 entity 高相似度预筛 — 语义高度重复直接 UPDATE 不调 LLM
        if scored_existing and scored_existing[0][0] > 0.92:
            top = scored_existing[0][1]
            new_conf = max(top.get("confidence", 0.7), confidence)
            await self.update_long_term(top["id"], content_text, new_conf, namespace, topic, entity)
            logger.debug("[memory] entity_replace 异entity语义去重: sim=%.3f '%s'", scored_existing[0][0], content_text[:30])
            return "UPDATE"

        existing = [item[1] for item in scored_existing[:3]]

        if not existing:
            # 同一主题无相关旧记忆，直接 ADD 写入目标命名空间
            await self.write_long_term(content_text, {"type": mem_type}, confidence, namespace, topic, entity)
            return "ADD"

        # 2. 同主题判定：极其相似直接 IGNORE
        top_match = existing[0]
        top_content = top_match["content"].strip().lower()
        new_content_clean = content_text.strip().lower()

        if top_content == new_content_clean:
            return "IGNORE"

        # 2.5 停用词清洗后的 Jaccard 精确校验（过滤泛词"喜欢/爱/用/在"等污染）
        new_tokens = _extract_token_set(content_text)
        old_tokens = _extract_token_set(str(top_match.get("content", "")))
        token_union = len(new_tokens | old_tokens)
        jaccard = len(new_tokens & old_tokens) / max(1, token_union) if token_union else 0.0

        if scored_existing and scored_existing[0][0] > 0.85 and jaccard >= 0.25:
            boosted = max(confidence, top_match.get("confidence", 0.0) + 0.05)
            await self.update_long_term(top_match["id"], content_text, boosted, namespace, topic, entity)
            return "UPDATE"

        # 3. 调用 LLM 进行同主题判决
        if provider:
            try:
                consolidation_prompt = _build_consolidation_prompt(content_text, existing)
                messages = [
                    LLMMessage(role="system", content=consolidation_prompt),
                    LLMMessage(role="user", content="请判定记忆操作，严格按照 JSON 格式输出。"),
                ]
                response = await provider.chat(messages, temperature=0.1, max_tokens=256)
                json_str = _extract_json(response.content)
                if json_str:
                    data = json.loads(json_str)
                    action = str(data.get("action", "ADD")).upper()
                    target_id = data.get("target_id")

                    if action == "UPDATE" and target_id:
                        new_text = data.get("content", content_text).strip()
                        await self.update_long_term(target_id, new_text, confidence, namespace, topic, entity)
                        return "UPDATE"
                    elif action == "DELETE" and target_id:
                        await self.delete_long_term(target_id)
                        return "DELETE"
                    elif action == "IGNORE":
                        return "IGNORE"
                    elif action == "ADD":
                        await self.write_long_term(content_text, {"type": mem_type}, confidence, namespace, topic, entity)
                        return "ADD"
            except Exception as e:
                logger.warning("LLM 判决解析失败，回退规则: %s", e)

        # 4. 保底规则判决（必须同 Topic 且 (属于单值槽位 或 高 Jaccard 重叠) 方可更新）
        if scored_existing:
            if topic in SINGLE_VALUED_TOPICS or (scored_existing[0][0] > 0.25 and jaccard >= 0.40):
                await self.update_long_term(top_match["id"], content_text, confidence, namespace, topic, entity)
                return "UPDATE"

        # 默认直接 ADD 写入
        # 维度四：同 Topic 竞争衰减（entity_replace 场景下新 entity 对旧 entity 压权重）
        await self._apply_competitive_decay(topic, exclude_entity=entity or "")
        await self.write_long_term(content_text, {"type": mem_type}, confidence, namespace, topic, entity)
        return "ADD"

    # ── Phase 19: 记忆压缩 ────────────────────────────────────

    async def compress_memories(self, provider, mode: str = "daily") -> int:
        """对慢衰减 topic 的碎片记忆做聚类归纳（压缩）。

        操作对象：shared_profile + work_tasks 下的 COMPRESSIBLE_TOPICS（非事件、非 single）。
        跳过对象：daily_life 下的事件类 topic（event_* / health_condition 靠激进遗忘自清理）。

        触发条件：同 topic 下 active 记忆 ≥ COMPRESS_MIN_FRAGMENTS（默认 8）条。
        流程：LLM 归纳 → 新摘要写入 → 旧碎片降权（confidence × 0.3）。

        Returns:
            成功压缩的 topic 数量。
        """
        if self._compressing:
            return 0
        self._compressing = True

        try:
            # 仅压缩 shared_profile + 当前模式命名空间（排除 daily_life 的事件 topic）
            namespaces = self.get_namespaces(mode)
            compress_ns = [ns for ns in namespaces if ns != "daily_life"]

            total = 0
            for ns in compress_ns:
                all_memories = _db.query_memories(ns, min_confidence=0.0)

                # 按 topic 分组
                grouped: dict[str, list[dict]] = {}
                for m in all_memories:
                    topic = _normalize_topic(m.get("topic"))
                    if topic not in COMPRESSIBLE_TOPICS:
                        continue
                    grouped.setdefault(topic, []).append(m)

                for topic, mems in grouped.items():
                    if len(mems) < COMPRESS_MIN_FRAGMENTS:
                        continue

                    # LLM 归纳
                    compressed = await self._llm_compress_topic(provider, topic, mems)
                    if not compressed or len(compressed) < 4:
                        continue

                    # 新摘要取均值置信度（上限 0.95）
                    avg_conf = sum(m.get("confidence", 0.5) for m in mems) / len(mems)
                    new_conf = min(0.95, max(0.70, avg_conf))
                    # 取第一条记忆的 entity 作为代表
                    entity = mems[0].get("entity", "") or topic

                    await self.write_long_term(
                        content=compressed,
                        metadata={"type": "preference", "compressed": True, "fragments": len(mems)},
                        confidence=new_conf,
                        namespace=ns,
                        topic=topic,
                        entity=entity,
                    )

                    # 旧碎片降权（不打删除，保留可追溯性）
                    for m in mems:
                        new_conf = max(0.10, m.get("confidence", 0.5) * 0.3)
                        _db.update_memory(m["id"], confidence=new_conf)

                    total += 1
                    logger.info("%s: %d fragments → 1 summary (conf=%.2f)", topic, len(mems), new_conf)

            if total > 0:
                logger.info("压缩完成: %d topics compressed", total)
            return total
        except Exception as e:
            logger.error("记忆压缩失败: %s", e)
            return 0
        finally:
            self._compressing = False

    async def _llm_compress_topic(self, provider, topic: str, memories: list[dict]) -> str:
        """调用 LLM 对同 topic 碎片记忆做归纳，返回精炼摘要文本。"""
        prompt = _build_compression_prompt(topic, memories)
        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content="请归纳以上碎片记忆，仅输出归纳后的文本。"),
        ]
        try:
            response = await provider.chat(messages, temperature=0.2, max_tokens=256)
            result = response.content.strip()
            # 清理可能残留的 markdown 标记
            if result.startswith("```"):
                result = result.split("\n", 1)[-1] if "\n" in result else result[3:]
            if result.endswith("```"):
                result = result[:-3]
            return result.strip()
        except Exception as e:
            logger.error("LLM 归纳 %s 失败: %s", topic, e)
            return ""

    @property
    def _should_compress(self) -> bool:
        """检查是否应触发压缩（每 COMPRESS_CHECK_INTERVAL 轮）。"""
        return self._compress_counter >= COMPRESS_CHECK_INTERVAL

    # ── Phase 19: 语义冲突仲裁 ────────────────────────────────

    async def detect_semantic_conflicts(self, provider, recent_memory: str, namespace: str, mem_type: str) -> int:
        """对新写入的记忆做时间窗口语义矛盾检测。

        检测范围：同 namespace + 同 type 的最近 CONFLICT_SCAN_LIMIT 条记忆。
        触发条件：每 CONFLICT_CHECK_INTERVAL 条新记忆执行一次。

        LLM 判决结果处理：
        - preference_upgrade → 旧记忆 confidence × 0.5
        - contradiction → 两条都 confidence × 0.5 + 控制台日志
        - state_change → 保留不变
        - no_conflict → 不做处理

        Returns:
            检测到的冲突数（0 或无）。
        """
        if self._detecting_conflicts or not provider:
            return 0
        self._detecting_conflicts = True

        try:
            # 取同 namespace 的最近记忆
            all_memories = _db.query_memories(namespace, min_confidence=0.0)
            # 过滤同 type
            same_type = [m for m in all_memories if m.get("type") == mem_type]
            # 按 updated_at 降序取最近 N 条
            same_type.sort(key=lambda m: m.get("updatedAt", 0), reverse=True)
            recent = same_type[:CONFLICT_SCAN_LIMIT]

            if len(recent) < 2:
                return 0

            # 调用 LLM 判决
            prompt = _build_conflict_detection_prompt(recent_memory, recent)
            messages = [
                LLMMessage(role="system", content=prompt),
                LLMMessage(role="user", content="请判定记忆冲突关系，严格按照 JSON 格式输出。"),
            ]
            response = await provider.chat(messages, temperature=0.1, max_tokens=256)
            json_str = _extract_json(response.content)

            if not json_str:
                return 0

            data = json.loads(json_str)
            conflict_type = str(data.get("type", "no_conflict")).strip()

            if conflict_type == "no_conflict":
                return 0

            target_id = data.get("target_id", "").strip()

            if conflict_type == "preference_upgrade" and target_id:
                # 旧偏好被升级 → 降权（保留可追溯）
                old = next((m for m in recent if m["id"].startswith(target_id[:12])), None)
                if old:
                    new_conf = max(0.15, old.get("confidence", 0.5) * 0.5)
                    _db.update_memory(old["id"], confidence=new_conf)
                    logger.info("preference_upgrade: %s conf %.2f -> %.2f", old["id"][:16], old.get("confidence", 0), new_conf)
                    return 1

            elif conflict_type == "contradiction" and target_id:
                # 逻辑矛盾 → 双方降权 + 记录日志
                old = next((m for m in recent if m["id"].startswith(target_id[:12])), None)
                if old:
                    new_conf_old = max(0.15, old.get("confidence", 0.5) * 0.5)
                    _db.update_memory(old["id"], confidence=new_conf_old)
                    # 同时降权新记忆（通过更新 confidence）
                    logger.warning("contradiction detected: new='%s...' vs old='%s...'", recent_memory[:50], old["content"][:50])
                    logger.info("both demoted: old conf %.2f -> %.2f", old.get("confidence", 0), new_conf_old)
                    return 1

            elif conflict_type == "state_change":
                logger.info("state_change acknowledged (kept both): '%s...'", recent_memory[:50])
                return 0

            return 0
        except Exception as e:
            logger.error("冲突检测失败: %s", e)
            return 0
        finally:
            self._detecting_conflicts = False

    async def update_behavior_profile(self, provider, mode: str = "daily") -> bool:
        """从长期记忆中提炼结构化行为画像（Phase 19）。

        触发条件：每 PROFILE_UPDATE_INTERVAL 轮。
        存储方式：复用 memories 表，topic=behavior_profile, type=user_profile。
        特性：永久不过期（decay=1.0），始终 UPDATE（合并策略=single）。
        """
        if not provider:
            return False

        try:
            # 获取所有命名空间的记忆作为素材
            namespaces = self.get_namespaces(mode)
            all_memories: list[dict] = []
            for ns in namespaces:
                all_memories.extend(_db.query_memories(ns, min_confidence=0.2))

            if len(all_memories) < 5:
                return False  # 记忆太少，无法提炼画像

            # 按置信度排序取 top 30
            all_memories.sort(key=lambda m: m.get("confidence", 0), reverse=True)
            sample = all_memories[:30]

            # 获取已有画像（如果有）
            existing_profile = ""
            for ns in namespaces:
                existing = _db.query_memories(ns, min_confidence=0.0)
                for m in existing:
                    if m.get("topic") == "behavior_profile":
                        existing_profile = m.get("content", "")
                        break

            # LLM 提炼
            prompt = _build_profile_prompt(sample, existing_profile)
            messages = [
                LLMMessage(role="system", content=prompt),
                LLMMessage(role="user", content="请提炼用户行为画像，仅输出 JSON。"),
            ]
            response = await provider.chat(messages, temperature=0.2, max_tokens=512)
            json_str = _extract_json(response.content)

            if not json_str:
                return False

            # 校验 JSON
            data = json.loads(json_str)
            # 重新序列化为紧凑 JSON 存储
            profile_content = json.dumps(data, ensure_ascii=False)

            # 写入记忆库（走 consolidate 确保 single 策略 UPDATE）
            action = await self.consolidate_memory(
                provider=None,  # 不走 LLM 判决链，直接 single UPDATE
                content_text=profile_content,
                mem_type="user_profile",
                confidence=0.95,
                namespace="shared_profile",
                topic="behavior_profile",
                entity="behavior_profile",
            )

            if action in ("ADD", "UPDATE"):
                logger.info("behavior profile updated (%d chars)", len(profile_content))
                return True

            return False
        except Exception as e:
            logger.error("profile update failed: %s", e)
            return False

    def purge_stale_event_memories(self) -> int:
        """清理深度衰减的旧事件记忆（Phase 19 · 防止 DB 膨胀）。

        仅清理同时满足以下条件的记忆：
        - topic 属于事件类（event_* / health_condition）
        - 创建时间超过 EVENT_PURGE_DAYS 天
        - 原始 confidence 已低于 EVENT_PURGE_CONFIDENCE

        Returns:
            删除的记忆条数。
        """
        import time as _time
        EVENT_TOPICS = {
            "event_social", "event_daily", "event_entertainment",
            "event_travel", "event_life", "health_condition",
        }
        now_ms = int(_time.time() * 1000)
        cutoff_ms = now_ms - (EVENT_PURGE_DAYS * 86400_000)
        total_deleted = 0

        namespaces = ["daily_life"]
        for ns in namespaces:
            all_memories = _db.query_memories(ns, min_confidence=0.0)
            for m in all_memories:
                topic = _normalize_topic(m.get("topic"))
                if topic not in EVENT_TOPICS:
                    continue
                created = m.get("createdAt", 0)
                confidence = m.get("confidence", 1.0)
                if created < cutoff_ms and confidence <= EVENT_PURGE_CONFIDENCE:
                    _db.delete_memory(m["id"])
                    total_deleted += 1

        if total_deleted > 0:
            logger.info("清理了 %d 条过期事件记忆（>%d天, conf<=%.2f）", total_deleted, EVENT_PURGE_DAYS, EVENT_PURGE_CONFIDENCE)
        return total_deleted

    # ── Phase 13: 时效语言分级与 Prompt 格式化 ──────────────

    @staticmethod
    def _get_staleness_tier(days_since_access: float) -> str:
        """根据距上次访问的天数返回时效等级。

        Returns:
            "active" (<7天) | "recent" (7-90天) | "stale" (90-365天) | "archived" (>365天)
        """
        if days_since_access < 7:
            return "active"
        elif days_since_access < 90:
            return "recent"
        elif days_since_access < 365:
            return "stale"
        return "archived"

    @staticmethod
    def _format_memories_for_prompt(recalled: list[dict]) -> str:
        """将召回的记忆列表格式化为 LLM 注入文本，含时效语言标记。

        按 topic 分组，同 topic 内按时效 + 置信度排序，
        添加「目前/最近/此前」中文时效标记。
        """
        if not recalled:
            return ""

        TIER_LABELS = {
            "active": "目前",
            "recent": "最近",
            "stale": "此前",
        }

        # 按 topic 分组
        grouped: dict[str, list[dict]] = {}
        for m in recalled:
            topic = m.get("topic", "general")
            grouped.setdefault(topic, []).append(m)

        lines: list[str] = []
        for topic, mems in sorted(grouped.items()):
            # 组内排序：active > recent > stale，同 tier 按 confidence 降序
            tier_order = {"active": 0, "recent": 1, "stale": 2, "archived": 3}
            mems.sort(key=lambda m: (
                tier_order.get(PersonalMemoryManager._get_staleness_tier(
                    m.get("_days_since_access", 999)), 99),
                -(m.get("confidence", 0))
            ))

            for m in mems:
                days = m.get("_days_since_access", 999)
                tier = PersonalMemoryManager._get_staleness_tier(days)
                if tier == "archived":
                    continue  # 已归档的不注入
                label = TIER_LABELS.get(tier, "")
                conf = m.get("confidence", 0)
                content = m["content"]
                lines.append(f"- [{m.get('type', '偏好')}] {label}{content}（置信度 {conf:.2f}）")

        if not lines:
            return ""

        return (
            "\n## 你对用户的长期记忆\n" + "\n".join(lines) + "\n\n"
            "【记忆使用铁律 — 违反即 OOC】\n"
            "- 以下记忆来自**用户对你的亲口陈述**，用「你之前跟我说过…」「我记得你提到过…」的口吻直接转述。\n"
            "- **绝对禁止**在记忆回复中添加以下内容：\n"
            "  1. 你作为流萤/萨姆的角色经历（「在远处观察」「我当时在执行任务」等）— 这是用户的记忆，不是你的剧情\n"
            "  2. 你的比喻习惯（「像萤火虫」「像星星」「像夜空」等）— 记忆回复不是角色扮演场景\n"
            "  3. 任何剧本/世界观元素（「星核」「命途」「艾利欧的剧本」「格拉默」等）— 用户没说过就不能加\n"
            "  4. 虚构的你与用户共同经历 — 你没和用户去过科技馆，不要说「那天我们…」\n"
            "- 正确的回答像一面镜子：用户说了什么，你复述什么。不加戏、不美化、不把你的角色设定塞进别人的故事里。"
        )

    # ── 记忆抽取与辅助原语 ───────────────────────────────────

    async def extract_memories(
        self,
        provider,
        recent_messages: list[LLMMessage],
        mode: str = "daily",
        on_complete: Optional[Any] = None,
    ) -> int:
        """LLM 驱动的自动记忆提取 + 两阶段双保险路由 + Mem0 生命周期整合。"""
        if not self.settings.memory.long_term_enabled:
            return 0
        if self._extracting:
            self._pending_context = (provider, recent_messages, mode, on_complete)
            logger.info("[memory] 并发抽取请求已挂起为待处理任务")
            return 0
        self._extracting = True
        self._message_count = 0  # 提前重置，防止等待 LLM 期间新消息二次触发

        try:
            default_ns = "work_tasks" if mode == "work" else "daily_life"
            extraction_prompt = _build_extraction_prompt(recent_messages)
            logger.info("[memory] 开始记忆抽取，窗口=%d 条消息，mode=%s", len(recent_messages), mode)
            extraction_messages = [
                LLMMessage(role="system", content=extraction_prompt),
                LLMMessage(role="user", content="请从以上对话中提取长期记忆，严格按照 JSON 格式输出。"),
            ]

            try:
                response = await provider.chat(extraction_messages, temperature=0.3, max_tokens=1024)
                content = response.content.strip()
                logger.debug("[memory] LLM 响应 %d 字符: %.100s", len(content), content)

                json_str = _extract_json(content)
                if not json_str:
                    logger.warning("[memory] JSON 解析失败，raw=%.200s", content[:200])
                    return 0

                data = json.loads(json_str)
                memories = data.get("memories", [])
                if not isinstance(memories, list):
                    logger.warning("[memory] LLM 返回格式异常: %s", type(memories))
                    return 0

                logger.info("[memory] LLM 返回 %d 条候选记忆", len(memories))
                if len(memories) == 0:
                    logger.info("[memory] 提取候选为空（近期对话未匹配到符合条件的记忆实体）")
                saved = 0
                for mem in memories:
                    if not isinstance(mem, dict):
                        continue
                    content_text = mem.get("content", "").strip()
                    confidence = float(mem.get("confidence", 0))
                    mem_type = str(mem.get("type", "user_profile"))
                    scope = str(mem.get("scope", "auto")).lower()
                    topic = str(mem.get("topic", "")).strip() or None
                    entity = str(mem.get("entity", "")).strip() or None

                    if not content_text:
                        continue

                    # ── 两阶段通用属性路由判定 ──────────────────────────
                    # 规则1: LLM 明确标注 universal + 类型为 user_profile/preference
                    # 规则2: 包含精准身份/设备名词关键词（注意：UNIVERSAL_KEYWORDS 不含「喜欢/偏好」泛词）
                    content_lower = content_text.lower()
                    is_universal_llm = (scope == "universal" and mem_type in ("user_profile", "preference"))
                    is_universal_rule = any(kw in content_lower for kw in UNIVERSAL_KEYWORDS)

                    # OR 语义：LLM 判 universal 或关键词命中任一满足即路由全局
                    if is_universal_llm or is_universal_rule:
                        target_ns = "shared_profile"
                    else:
                        target_ns = default_ns

                    # 走 Mem0 主题隔离整合链
                    action = await self.consolidate_memory(
                        provider=provider,
                        content_text=content_text,
                        mem_type=mem_type,
                        confidence=confidence,
                        namespace=target_ns,
                        topic=topic,
                        entity=entity,
                    )
                    if action in ("ADD", "UPDATE", "DELETE"):
                        saved += 1
                        logger.info("[memory] 记忆已%s: type=%s topic=%s entity=%s conf=%.2f",
                                    action, mem_type, topic, entity, confidence)
                        # Phase 19: 冲突检测触发（每 10 条新记忆）
                        self._conflict_counter += 1
                        if self._conflict_counter >= CONFLICT_CHECK_INTERVAL and action == "ADD":
                            self._conflict_counter = 0
                            import asyncio
                            asyncio.create_task(
                                self.detect_semantic_conflicts(provider, content_text, target_ns, mem_type)
                            )

                self._message_count = 0

                # Phase 19: 压缩触发（异步，不阻塞）
                self._compress_counter += 1
                if self._should_compress:
                    self._compress_counter = 0
                    import asyncio
                    asyncio.create_task(self.compress_memories(provider, mode))

                # Phase 19: 行为画像更新（异步，每 40 轮）
                self._profile_counter += 1
                if self._profile_counter >= PROFILE_UPDATE_INTERVAL:
                    self._profile_counter = 0
                    import asyncio
                    asyncio.create_task(self.update_behavior_profile(provider, mode))

                # Phase 19: 过期事件记忆清理（每 100 轮）
                self._purge_counter += 1
                if self._purge_counter >= EVENT_PURGE_INTERVAL:
                    self._purge_counter = 0
                    deleted = self.purge_stale_event_memories()
                    if deleted > 0:
                        logger.info("auto-cleanup done: %d stale events removed", deleted)

                if saved > 0 and on_complete:
                    try:
                        import inspect
                        if inspect.iscoroutinefunction(on_complete):
                            await on_complete(saved)
                        else:
                            on_complete(saved)
                    except Exception as e_cb:
                        logger.warning("[memory] on_complete 回调执行异常: %s", e_cb)

                return saved
            except Exception as e:
                logger.exception("记忆抽取与整合失败: %s", e)
                return 0
        finally:
            self._extracting = False
            if self._pending_context:
                p, msgs, m, cb = self._pending_context
                self._pending_context = None
                logger.info("[memory] 开始执行挂起的并发抽取任务...")
                import asyncio
                asyncio.create_task(self.extract_memories(p, msgs, m, on_complete=cb))

    def save_chat_message(self, session_id: str, role: str, content: str, mode: str, emotion: Optional[str] = None):
        _db.save_message(session_id, role, content, mode, emotion)

    @property
    def should_extract(self) -> bool:
        """至少积累 N 条消息后才触发提取，间隔由 memory_extraction_interval 控制。

        延迟 2 代记忆问题：设置阈值太低（如每轮都提取）会导致 LLM
        把同一事实以不同措辞重复写入，因为提取 prompt 中看不到已有记忆。
        """
        threshold = max(2, self.settings.memory.memory_extraction_interval)
        return self._message_count >= threshold


def _build_extraction_prompt(recent_messages: list[LLMMessage]) -> str:
    # 提取时仅使用 user 的真实发言，不混入 assistant 的回复
    dialogue = "\n".join(m.content for m in recent_messages if m.role == "user")
    return f"""请从以下对话中提取关于用户 (user) 的个人信息、人际关系与事件事实，写入记忆库。

## 什么应该提取
只要用户在对话中**明确说过**，就值得记录：
- **偏好/爱好**：用户明确说过「我喜欢/爱/习惯 X」（如「喜欢大海」「喜欢蓝色」「喜欢游泳」「喜欢吃冰淇淋」「喜欢喝咖啡」），首次提及即提取
  - 强偏好信号（「非常喜欢/最爱/特别喜欢/超爱」）→ confidence = 0.95
  - 例如「我非常喜欢明朝的历史」→ preference / entertainment_hobby / 明朝 / 用户非常喜欢明朝的历史 / confidence=0.95
- **人际关系**：用户提到的家人、亲戚、朋友、同学、同事等关系，entity 填人名 → confidence = 0.95
  - 例如「小美是我的姐姐」→ relationship / relationship_family / 小美 / 小美是用户的姐姐 / confidence=0.95
- **社交与行程事件**：用户提到和某人一起做了什么，或去某地旅游/出差（如「和小美去游泳」「和小刚去重庆旅游」）
  - 例如「我昨天和小刚一起去重庆旅游了」→ event / event_travel / 小刚 / 用户和小刚一起去重庆旅游 / confidence=0.95
- **身份信息**：姓名、生日、家乡、职业等
- **学习/工作**：正在学什么、做什么项目

## 绝对不能提取
- 临时意图/一次性计划（「明天去吃火锅」）— 区别于已发生事件（「昨天去游泳了」）
- 纯情绪表达（「我心情不错」）
- 角色扮演/游戏世界观内容（星神、命途、星球名、角色名等）
- **助手/角色自身的偏好或身世**（绝对禁止提取流萤/萨姆自身的偏好如「喜欢橡木蛋糕卷」、身世「来自格拉默」、生理状态「失熵症」等为用户的记忆，它们属于角色本身，严禁存入用户的 shared_profile）
- 同一事实的重复（已在记忆库中的跳过）
- **注意**：用户对真实事物的偏好（如「喜欢历史」「喜欢唐朝/明朝」「喜欢大海」）或真实地点旅游（如「去重庆旅游」）是**真实事实**，不是游戏世界观内容，必须提取

## 门槛：所有记忆 confidence ≥ 0.75，低于的不要返回

## 记忆类型 (type) 与 Topic 速查表
### 偏好类（type=preference, scope=universal）
- dietary_preference: 饮食/食品/饮料偏好或忌口
- entertainment_hobby: 运动/休闲/色彩/历史/文学/长期爱好（如「喜欢大海」「喜欢蓝色」「喜欢明朝的历史」）
- device_environment: 习惯调用的设备/操作系统/开发软件

### 身份类（type=user_profile, scope=universal）
- identity_hometown: 家乡/籍贯/居住地（如「温州人」）
- identity_job: 职业/身份/专业

### 关系类（type=relationship, scope=universal）
- relationship_family: 关于家人（爸爸/妈妈/姐姐/妹妹/哥哥/弟弟等）的关系
- relationship_friend: 关于朋友/同学/室友的关系
- relationship_colleague: 关于同事/领导/导师的关系

### 事件类（type=event, scope=daily_only, 精简摘要 ≤ 2 句）
- event_social: 社交事件 — 和人一起做的事（「和小美去游泳」）
- event_daily: 日常琐事（「去了趟超市」）
- event_entertainment: 娱乐事件（「看了场电影」）
- event_travel: 出行/旅游事件（「和小刚去重庆旅游」「去杭州出差」）
- event_life: 人生里程碑

## 作用域 (scope)
- universal: 跨场景通用（设备/系统/身份/关系/色彩与爱好偏好/饮食禁忌等）
- work_only: 仅限工作（工作项目、代码风格、项目架构）
- daily_only: 仅限日常（事件、健康、学习技能）

## 对话内容
{dialogue}

## 输出格式（严格 JSON，禁止换行，禁止注释）
{{"memories":[{{"type":"preference","scope":"universal","topic":"entertainment_hobby","entity":"明朝","content":"用户非常喜欢明朝的历史","confidence":0.95}},{{"type":"relationship","scope":"universal","topic":"relationship_family","entity":"小美","content":"小美是用户的姐姐","confidence":0.95}},{{"type":"event","scope":"daily_only","topic":"event_travel","entity":"小刚","content":"用户和小刚一起去重庆旅游","confidence":0.95}}]}}"""


def _extract_json(text: str) -> Optional[str]:
    import re
    text = text.strip()
    # 剥离 <think>...</think> 思考过程（防止思考块内的花括号干扰 JSON 提取）
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        return m.group(1)
    m = re.search(r"\{[^{}]*\"memories\"[\s\S]*\}", text)
    if m:
        return m.group(0)
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return m.group(0)
    return None


def _tokenize_query(query: str) -> list[str]:
    """将查询切分为关键词用于 keyword boost。

    提取 2-gram + 英文词 + 单中文字符（过滤高频停用词）。
    """
    import re
    query = query.lower().strip()
    if not query:
        return []

    # 中文停用词（无信息量的高频单字）
    _STOP_CHARS = set("的了吗呢啊吧呀哦嗯么和与这不就会也都要有没能很去来说看可对到但让把被从给向在是我他她它")

    tokens: list[str] = []
    eng_words = re.findall(r"[a-z0-9]+", query)
    tokens.extend(eng_words)

    cjk_chars = re.findall(r"[\u4e00-\u9fff]+", query)
    for chunk in cjk_chars:
        # 单字符：仅保留非停用词
        for ch in chunk:
            if ch not in _STOP_CHARS:
                tokens.append(ch)
        # 2-gram
        for i in range(len(chunk) - 1):
            tokens.append(chunk[i:i + 2])

    return list(dict.fromkeys(tokens))  # 去重保序


def _extract_token_set(text: str) -> set[str]:
    """提取中文 1/2-gram + 英文/数字 token 集合，过滤 STOP_WORDS 泛词，用于精确 Jaccard 重叠率计算。"""
    import re
    text = text.lower().strip()
    # 1. 预处理：先全量剥离文本中的停用词与通用虚词动词
    for sw in STOP_WORDS:
        text = text.replace(sw, "")

    tokens: set[str] = set()
    eng_words = re.findall(r"[a-z0-9]+", text)
    for w in eng_words:
        tokens.add(w)
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]+", text)
    for chunk in cjk_chunks:
        for char in chunk:
            tokens.add(char)
        for i in range(len(chunk) - 1):
            tokens.add(chunk[i:i + 2])
    return tokens


def _build_consolidation_prompt(new_content: str, existing_memories: list[dict]) -> str:
    existing_str = "\n".join(
        f"- ID: {m['id']} | 内容: {m['content']}" for m in existing_memories
    )
    return f"""你是一个记忆整合助手。请评估新发现的用户信息与已有的旧记忆之间的关系，并做出生效判定。

## 新抽取的信息
"{new_content}"

## 相关的已有旧记忆
{existing_str}

## 判定规则 (action)
1. UPDATE: 新信息与某条旧记忆话题相同，但内容发生了变更/更新（例如从 Windows 变为 Mac），此时需要用新信息更新该旧条目，返回 target_id。
2. DELETE: 新信息表明某条旧记忆已完全失效/不成立，需删除该旧条目，返回 target_id。
3. IGNORE: 新信息与某条旧记忆完全重复或已包含，无需做任何改动。
4. ADD: 新信息是完全独立的全新事实，与已有旧记忆无冲突。

## 输出格式（严格 JSON）
{{"action":"UPDATE","target_id":"mem-123456","content":"用户偏好使用 Mac 电脑进行日常开发"}}"""
