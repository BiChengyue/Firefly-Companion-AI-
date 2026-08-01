"""HSRChat 剧情知识库 — Query 归一化规则引擎。

三层架构的第二层：将用户自然语言输入转换为结构化查询，
再交给底层 FTS5 + ONNX 混合检索引擎。

原则：零 LLM 参与，全部规则引擎（字典 + 正则），0ms 延迟、0 token 成本。
"""
import logging
import re as _re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("hsr_lore.normalizer")

# ═══════════════════════════════════════════════════════
# Phase 1 — 三层别名映射
# ═══════════════════════════════════════════════════════

# ── 第一层：角色专属绰号（1:1 精确映射）──
# 用户说的 → 规范角色名（确信唯一对应）
_CHARACTER_NICKNAMES: dict[str, str] = {
    # === 翁法罗斯 ===
    "金丝": "阿格莱雅",
    "金织": "阿格莱雅",
    "改衣师": "阿格莱雅",
    "救世主": "白厄",
    "白发剑士": "白厄",
    "卡厄斯兰那": "白厄",
    "至黑之剑": "白厄",
    "负世火种": "白厄",
    "背负黑夜的白发剑士": "白厄",
    "纷争火种": "万敌",
    "不死途": "不死途",
    "死龙": "遐蝶",
    "死河": "遐蝶",
    "神悟树庭的老师": "那刻夏",
    "学者": "那刻夏",
    "医生": "风堇",
    "吟游诗人": "赛飞儿",
    "猫耳侠盗": "赛飞儿",
    "拿金币的猫耳侠盗": "赛飞儿",
    "海妖": "海瑟音",
    "缇里西庇俄丝": "缇宝",
    "迷迷": "迷迷",
    "门扉火种": "缇宝",
    "悬锋城的狮子": "万敌",
    "狮子王": "万敌",
    # === 匹诺康尼 ===
    "钟表小子": "钟表匠",
    "米哈伊尔": "钟表匠",
    "大丽花": "大丽花",
    "忆者": "黑天鹅",
    # === 仙舟 ===
    "盲眼剑客": "镜流",
    "龙尊": "丹恒",
    "饮月君": "丹恒",
    # === 通用称谓 ===
    "星核猎手": "星核猎手",
    "黄金裔": "黄金裔",
    "无名客": "无名客",
    "开拓者": "开拓者",
}

# ── 第二层：派系/职业/群体称谓（触发派系知识搜索，不绑定个体）──
# 用户说的 → 数据库/chunks 中实际存在的搜索词
_FACTION_TERMS: dict[str, str] = {
    "忆者": "流光忆庭",
    "焚化工": "焚化工",
    "假面愚者": "假面愚者",
    "纯美骑士团": "纯美骑士团",
    "博识学会": "博识学会",
    "星核猎手": "星核猎手",
    "无名客": "无名客",
    "自灭者": "自灭者",
    "天才俱乐部": "天才俱乐部",
    "流光忆庭": "流光忆庭",
    "家族": "家族",
    "星际和平公司": "星际和平公司",
    "黄金裔": "黄金裔",
    "半神议院": "半神议院",
    "云上五骁": "云上五骁",
    "持明": "持明",
    "丰饶民": "丰饶民",
}

# ── 第三层：歧义/泛化概念（触发多实体搜索）──
# 概念词 → 应该搜哪些角色；空列表 → 搜对应分类（lore_aeon/lore_faction）
_AMBIGUOUS_CONCEPTS: dict[str, list[str]] = {
    "火种": ["白厄", "阿格莱雅", "缇宝", "万敌", "遐蝶", "那刻夏", "风堇", "赛飞儿", "海瑟音", "刻律德菈"],
    "泰坦": ["纷争泰坦", "刻法勒", "欧洛尼斯", "德谬歌"],
    "命途": [],          # 空列表 → 搜 lore_aeon 分类
    "星神": ["阿基维利", "纳努克", "岚", "药师", "克里珀"],
    "遗器": [],          # gameplay 意图 → 零注入，但保留概念
    "光锥": [],          # gameplay 意图 → 零注入
}

# ═══════════════════════════════════════════════════════
# Phase 2 — 场景同义词 & 意图分类
# ═══════════════════════════════════════════════════════

_SCENE_SYNONYMS: dict[str, str] = {
    # 铁幕系列
    "铁幕": "铁幕大决战",
    "铁幕之战": "铁幕大决战",
    "铁幕决战": "铁幕大决战",
    "苍穹撕裂": "铁幕大决战",
    "最后大决战": "铁幕大决战",
    "黑潮决战": "铁幕大决战",
    "黑潮围城": "铁幕大决战",
    # 创世/逐火系列
    "创世大决战": "逐火之旅",
    "逐火之旅": "逐火之旅",
    # 人物关联场景
    "英雄浴池": "阿格莱雅",
    "辩论场": "白厄",
    "彩虹桥": "白厄",
    "树庭辩论": "那刻夏",
    "神悟树庭": "那刻夏",
    "悬锋城": "万敌",
    "哀丽秘榭": "白厄",
    "奥赫玛圣城": "阿格莱雅",
    "奥赫玛": "阿格莱雅",
    # 匹诺康尼场景
    "三度死亡": "神主日三度死亡",
    "第三次死亡": "第三次死亡真相",
    "流梦礁": "流梦礁重逢",
    "筑梦边境": "筑梦边境",
    "秘密基地": "秘密基地",
    "匹诺康尼": "匹诺康尼",
    # 翁法罗斯
    "翁法罗斯": "翁法罗斯",
    # 仙舟
    "仙舟罗浮": "仙舟罗浮",
    "罗浮": "仙舟罗浮",
    # 奇美拉/观星 → 遐蝶场景
    "奇美拉标本": "遐蝶",
    "观星": "遐蝶",
}

# ── 意图分类正则 ──

_SHORT_ENTITY = _re.compile(
    r"是谁|是啥|介绍|认识|知道|听说过|怎么样|评价|了解"
)
_DEEP_SCENE = _re.compile(
    r"剧情|任务|做了什么|做了啥|结局|发生|后来|过程|哪一章|"
    r"怎么回事|详细|经历过|场景|大决战|决战|大战|战场|经历|战斗|"
    r"最后|当时|那时|第一次|初次|初次见面|遇见"
)
_RELATION = _re.compile(
    r"关系|和.*什么|认识|跟.*熟|认识.*吗|认识|怎么认识"
)
_GAMEPLAY = _re.compile(
    r"遗器|光锥|配队|突破|材料|周本|星魂|行迹|"
    r"几命|几魂|满命|满魂|专武|什么遗器|带什么|用什么|"
    r"装备|面板|暴击|爆伤|速度|充能|击破|光锥池"
)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class NormalizedQuery:
    """归一化后的查询结构。"""
    entities: list[str] = field(default_factory=list)
    scene_keywords: list[str] = field(default_factory=list)
    faction_terms: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: list[tuple[str, list[str]]] = field(default_factory=list)
    intent: Literal["short_entity", "deep_scene", "relation", "opinion", "gameplay", "casual"] = "casual"
    category_hint: str | None = None
    is_firefly_present: bool = False


# ═══════════════════════════════════════════════════════
# 意图分类
# ═══════════════════════════════════════════════════════

def _classify_intent(user_message: str) -> str:
    """分类用户意图。gameplay 优先级最高，依次下降。"""
    if _GAMEPLAY.search(user_message):
        return "gameplay"

    is_question = bool(_re.search(r"[？?吗呢]|是谁|是啥|讲[讲下]|介绍|怎么样|怎么|知道|认识|了解", user_message))

    if _RELATION.search(user_message):
        return "relation"
    # opinion 必须在 short_entity 之前：评价同时命中两者但语义更偏意见
    if _re.search(r"你觉得|你认为|你怎看|你.{0,3}评价", user_message):
        return "opinion"
    if _DEEP_SCENE.search(user_message) and is_question:
        return "deep_scene"
    if _SHORT_ENTITY.search(user_message) and is_question:
        return "short_entity"
    return "casual"


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

def normalize_query(user_message: str,
                    context_entities: list[str] | None = None) -> NormalizedQuery:
    """用户原话 → 结构化查询。

    context_entities: 上一轮对话中已识别的实体（用于代词指代回退）。
    """
    q = NormalizedQuery()

    # ── 按 key 长度降序排序：优先匹配最长专有名词 ──
    sorted_nicknames = sorted(_CHARACTER_NICKNAMES.items(),
                              key=lambda x: -len(x[0]))
    sorted_factions = sorted(_FACTION_TERMS.items(),
                             key=lambda x: -len(x[0]))
    sorted_ambiguous = sorted(_AMBIGUOUS_CONCEPTS.items(),
                              key=lambda x: -len(x[0]))

    # 1. 角色绰号 → entities（最长优先）
    for alias, canonical in sorted_nicknames:
        if alias in user_message:
            q.entities.append(canonical)

    # 1.5 Fallback：合并 hsr_lore._ENTITY_ALIASES（已有的 100 条基础别名）
    try:
        from app.core.hsr_lore import _ENTITY_ALIASES as _BASE_ALIASES
        for alias, canonical in _BASE_ALIASES.items():
            if alias in user_message and canonical not in q.entities:
                q.entities.append(canonical)
    except ImportError:
        pass

    # 2. 派系词 → faction_terms（不加到 entities）
    for term, search_term in sorted_factions:
        if term in user_message:
            q.faction_terms.append((term, search_term))

    # 3. 歧义概念 → entities 前 3 个候选（最长优先）
    for concept, candidates in sorted_ambiguous:
        if concept in user_message:
            q.ambiguous.append((concept, candidates))
            if candidates:
                q.entities.extend(candidates[:3])

    # 4. 场景同义词 → scene_keywords
    for phrase, canonical in _SCENE_SYNONYMS.items():
        if phrase in user_message:
            q.scene_keywords.append(canonical)

    # 5. 额外的 2-4 字中文连续串作为备用 n-gram
    cjk_ngrams = _re.findall(r'[\u4e00-\u9fff]{2,4}', user_message)
    for ng in cjk_ngrams:
        if ng not in q.scene_keywords and ng not in q.entities:
            q.scene_keywords.append(ng)
    q.scene_keywords = list(dict.fromkeys(q.scene_keywords))[:5]

    # 6. 意图分类
    q.intent = _classify_intent(user_message)

    # 7. category_hint
    if q.intent == "short_entity":
        q.category_hint = "character"
    elif q.intent == "deep_scene":
        q.category_hint = "story_main"
    elif q.intent == "relation":
        q.category_hint = None  # 多跳验证接管
    elif q.faction_terms:
        q.category_hint = "lore_faction"
    elif q.intent == "gameplay":
        q.category_hint = None  # 零注入

    # 8. is_firefly_present 粗略判断
    q.is_firefly_present = any(
        kw in user_message for kw in ("我", "流萤", "萨姆", "咱们", "我们")
    )

    # 9. 代词回退：本轮无实体但上轮有 → 继承
    if not q.entities and context_entities:
        q.entities = list(context_entities)

    # 10. 去重
    q.entities = list(dict.fromkeys(q.entities))

    return q


# ═══════════════════════════════════════════════════════
# Phase 3.3 — 会话级实体回退缓存（多轮代词指代）
# ═══════════════════════════════════════════════════════

_last_entities: dict[str, list[str]] = {}


def set_session_entities(session_id: str, entities: list[str]) -> None:
    """保存当前轮识别的实体，供下一轮回退。"""
    if session_id and entities:
        _last_entities[session_id] = list(entities)


def get_session_entities(session_id: str) -> list[str]:
    """获取上轮识别的实体（本轮无实体时回退使用）。"""
    return _last_entities.get(session_id, [])
