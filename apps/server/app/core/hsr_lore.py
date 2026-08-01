"""HSRChat 本地剧情知识库 — 离线索引 + 混合检索 + 置信度闸门（方案 B）。

第二十八阶段重构，替代旧的"文件即索引 + 在线 grep"：
- 离线：scripts/build_lore_index.py 构建 data/lore_index.db
  （FTS5 全文索引 + ONNX 384 维向量，L0 亲历记忆/精选卡片 > L1~L4 wiki）
- 在线：FTS5 BM25 召回 ∥ 向量余弦召回 → RRF 融合 → 置信度分层：
    高置信 → 三层口吻注入（我记得 / 我见过 / 我听说）
    低置信 + 剧情类提问 → 防编造约束块（fail-closed）
    低置信 + 非剧情消息 → 空注入（正常聊天/工作零 token 增量）
- 检索常开、注入设闸：词表白名单闸门已废除。
"""

import logging
import re as _re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("hsr_lore")

# 延迟导入，避免循环依赖
_normalizer: Optional[object] = None


def _get_normalizer():
    global _normalizer
    if _normalizer is None:
        from app.core.hsr_lore_normalizer import normalize_query as _nq, \
            _SCENE_SYNONYMS as _ss, NormalizedQuery as _NQ
        _normalizer = type("_NZ", (), {
            "normalize_query": staticmethod(_nq),
            "SCENE_SYNONYMS": _ss,
            "NormalizedQuery": _NQ,
        })
    return _normalizer

# ── 路径常量 ──
_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # core/hsr_lore.py → 项目根
_DB_PATH_DEFAULT = _PROJECT_ROOT / "data" / "lore_index.db"

# ── 意图识别（保留自旧版）──

_CASUAL_SELF_WORDS = (
    "你喜欢", "你觉得", "你最", "你欣赏", "你怎看", "你认为", "你眼里",
    "你眼中的", "你对我", "你希望", "你在乎",
)

_LORE_QUESTION_WORDS = (
    "谁", "知道", "是什么", "设定", "剧情", "结局", "背景", "身份", "来自", "关系",
    "做了什么", "干了什么", "做了啥", "干啥", "干嘛", "干过什么",
    "怎么", "为什么", "哪", "多少", "技能", "命途", "星神",
    "介绍", "说说", "讲讲", "讲一下", "资料", "官网", "设定集", "起源",
    "死因", "真名", "阵营", "配音", "原型",
)

_ENTITY_ALIASES: dict[str, str] = {
    "银狼": "银狼", "卡芙卡": "卡芙卡", "刃": "刃", "星期日": "星期日",
    "知更鸟": "知更鸟", "花火": "花火", "黄泉": "黄泉", "翡翠": "翡翠",
    "加拉赫": "加拉赫", "大丽花": "大丽花", "黑天鹅": "黑天鹅",
    "翁法罗斯": "翁法罗斯", "开拓者": "开拓者", "三月七": "三月七",
    "丹恒": "丹恒", "姬子": "姬子", "瓦尔特": "瓦尔特",
    "布洛妮娅": "布洛妮娅", "希儿": "希儿", "符玄": "符玄", "景元": "景元",
    "镜流": "镜流", "丹枢": "丹枢", "白露": "白露", "杰帕德": "杰帕德",
    "克拉拉": "克拉拉", "娜塔莎": "娜塔莎", "彦卿": "彦卿",
    "罗刹": "罗刹", "托帕": "托帕", "砂金": "砂金",
    "云璃": "云璃", "飞霄": "飞霄", "椒丘": "椒丘",
    "貊泽": "貊泽", "乱破": "乱破", "渔阳": "渔阳",
    "艾利欧": "艾利欧", "螺丝咕姆": "螺丝咕姆",
    "阮·梅": "阮·梅", "真理医生": "真理医生",
    # 翁法罗斯黄金裔（含全名别名映射）
    "白厄": "白厄", "卡厄斯兰那": "白厄",
    "阿格莱雅": "阿格莱雅",
    "万敌": "万敌", "迈德谟斯": "万敌",
    "遐蝶": "遐蝶", "卡斯托里斯": "遐蝶",
    "那刻夏": "那刻夏", "阿那克萨戈拉斯": "那刻夏",
    "风堇": "风堇", "雅辛忒丝": "风堇",
    "赛飞儿": "赛飞儿", "赛法利娅": "赛飞儿",
    "海瑟音": "海瑟音", "海列屈拉": "海瑟音",
    "刻律德菈": "刻律德菈", "凯莉丝": "刻律德菈",
    "昔涟": "昔涟", "爱莉希雅": "昔涟",
    "缇宝": "缇宝", "缇里西庇俄丝": "缇宝",
    "长夜月": "长夜月",
    # 仙舟别名
    "格妮薇儿": "桂乃芬", "丹朱": "灵砂",
    "忘归人": "停云",
    "匹诺康尼": "匹诺康尼", "星穹列车": "星穹列车",
    "星核猎手": "星核猎手", "格拉默": "格拉默", "命途": "命途",
    "苏乐达": "苏乐达", "艾迪恩公园": "艾迪恩公园",
    "钟表小子": "钟表小子",
    "筑梦边境": "筑梦边境", "黄金的时刻": "黄金的时刻",
    "奥帝购物中心": "奥帝购物中心", "格拉克斯大道": "格拉克斯大道",
    "橡木蛋糕卷": "橡木蛋糕卷", "美梦剧团": "美梦剧团",
    "惊梦剧团": "惊梦剧团", "晖长石号": "晖长石号",
    "流梦礁": "流梦礁", "大剧院": "匹诺康尼大剧院",
    "苜蓿币": "苜蓿币", "鸢尾花家系": "鸢尾花家系",
    "猎犬家系": "猎犬家系", "糖浆主义": "糖浆主义",
    "星神": "星神", "巡猎": "巡猎", "丰饶": "丰饶", "毁灭": "毁灭",
    "同谐": "同谐", "终末": "终末", "虚无": "虚无", "繁育": "繁育",
    "智识": "智识", "存护": "存护", "记忆命途": "记忆命途",
    "流光忆庭": "流光忆庭", "联觉梦境": "联觉梦境", "家族": "家族",
    "谐乐大典": "谐乐大典", "热砂盛典": "热砂盛典", "仙舟": "仙舟",
    "持明": "持明", "云上五骁": "云上五骁", "焚化工": "焚化工",
    "何物朝向死亡": "何物朝向死亡", "忆域迷因": "忆域迷因",
    "星核": "星核", "星穹": "星穹", "虚数": "虚数", "裂界": "裂界",
    "智库": "智库", "黑塔": "黑塔", "博识学会": "博识学会",
    "公司": "星际和平公司", "丰饶民": "丰饶民",
    "雅利洛": "雅利洛-VI", "贝洛伯格": "贝洛伯格",
    "罗浮": "仙舟罗浮", "朱明": "仙舟朱明", "曜青": "仙舟曜青",
    "阿基维利": "阿基维利", "纳努克": "纳努克", "岚": "岚",
    "药师": "药师", "克里珀": "克里珀",
    # 绝灭大君/毁灭令使
    "绝灭大君": "绝灭大君", "幻胧": "幻胧",
    "星啸": "星啸", "焚风": "焚风", "铁墓": "铁墓", "归寂": "归寂",
    # 翁法罗斯
    "奥赫玛": "翁法罗斯·奥赫玛", "缇宝": "缇宝", "铁墓": "铁墓",
    # 匹诺康尼·梦主
    "梦主": "梦主", "歌斐木": "梦主",
    "黄金裔": "黄金裔", "黑潮": "黑潮", "泰坦": "泰坦", "纷争": "纷争泰坦",
    "来古士": "来古士", "尼卡多利": "纷争泰坦·尼卡多利", "迷迷": "迷迷",
    "刻法勒": "刻法勒", "欧洛尼斯": "欧洛尼斯",
    "盗火行者": "盗火行者", "再创世": "再创世",
    "缇宁": "缇宁", "缇安": "缇安", "德谬歌": "德谬歌", "丹枫": "丹枫",
    "半神议院": "半神议院", "逐火之旅": "逐火之旅",
    # 二相乐园
    "二相乐园": "二相乐园", "幻月游戏": "幻月游戏",
    "不死途": "不死途", "朽叶": "朽叶", "爻光": "爻光",
    "隆介": "隆介", "火花": "火花", "真珠": "真珠",
    "告死魔": "告死魔", "界外天魔": "界外天魔",
    "绘世学院": "二相乐园·绘世学院", "鸽川区": "二相乐园·鸽川区",
    "珠星大厦": "珠星大厦", "幻太子": "幻太子", "鳄鱼侦探": "鳄鱼侦探",
    "二次元JUMP": "二次元JUMP", "不死神探事务所": "不死神探事务所",
    "乔瓦尼": "乔瓦尼", "啵啵娃": "啵啵娃", "归寂": "归寂",
    "血涂游戏": "血涂游戏", "幸福手术": "幸福手术",
    # 补充
    "桑博": "桑博", "米莎": "米莎", "米哈伊尔": "钟表匠", "钟表匠": "钟表匠",
    # ── 雅利洛-VI 核心角色（jarilo_vi_lore.md 覆盖）──
    "可可利亚": "可可利亚", "希露瓦": "希露瓦", "史瓦罗": "史瓦罗",
    "虎克": "虎克", "卢卡": "卢卡", "佩拉": "佩拉",
    # ── 黑塔空间站核心角色 ──
    "艾丝妲": "艾丝妲", "阿兰": "阿兰",
    # ── 仙舟补充 ──
    "灵砂": "灵砂", "素裳": "素裳",
    # ── 匹诺康尼补充 ──
    "波提欧": "波提欧", "查德威克": "查德威克",
    # ── IPC / 特殊角色 ──
    "斯科特": "斯科特", "林登斯科特": "斯科特", "孤狼": "斯科特",
    # ── 星穹列车 ──
    "帕姆": "帕姆",
    # ── 仙舟角色（xianzhou_luofu_lore.md 覆盖）──
    "藿藿": "藿藿", "桂乃芬": "桂乃芬", "呼雷": "呼雷",
    "白珩": "白珩", "应星": "应星", "驭空": "驭空", "青雀": "青雀",
    "幻胧": "幻胧", "彩翼": "彩翼", "晴霓": "晴霓",
    "真德林": "真德林", "尾巴大爷": "尾巴大爷",
    # ── 仙舟派系/概念 ──
    "持明族": "持明族", "狐人族": "狐人族", "步离人": "步离人",
    "仙舟联盟": "仙舟联盟", "魔阴身": "魔阴身", "岁阳": "岁阳",
    "十王司": "十王司", "丹鼎司": "丹鼎司", "天舶司": "天舶司",
    "丰饶民": "丰饶民", "丰饶之民": "丰饶民",
    # ── 黑塔空间站概念 ──
    "模拟宇宙": "模拟宇宙", "末日兽": "末日兽", "奇物": "奇物",
    "以太涂鸦": "以太涂鸦", "斯蒂芬": "斯蒂芬", "阿弗利特": "阿弗利特",
    # ── 翁法罗斯角色 ──
    "皮西厄斯": "皮西厄斯", "小伊卡": "小伊卡",
    # ── 翁法罗斯概念 ──
    "逐火之旅": "逐火之旅", "火种": "火种", "黄金裔": "黄金裔",
    "黑潮": "黑潮", "泰坦十二神": "泰坦", "门扉": "门扉",
    "死龙": "死龙", "死龙·玻吕刻斯": "死龙", "帝皇权杖": "帝皇权杖",
    # ── 二相乐园 ──
    "九喵儿": "九喵儿", "普狸策": "普狸策",
    "欧泊": "欧泊", "美亚": "美亚", "素子": "素子",
    "幻月": "幻月", "差分宇宙": "差分宇宙",
    # ── 雅利洛-VI 补充 ──
    "大守护者": "大守护者", "帕斯卡": "帕斯卡",
    # ── 派系/组织/通用概念（用户高频查询，防闸门遗漏）──
    "银鬃铁卫": "银鬃铁卫", "地火": "地火", "云骑军": "云骑军",
    "药王秘传": "药王秘传", "反物质军团": "反物质军团", "烬灭军团": "反物质军团",
    "流光忆庭": "流光忆庭", "假面愚者": "假面愚者", "纯美骑士团": "纯美骑士团",
    "幻造种": "幻造种", "百变狸猫": "百变狸猫", "影鳄": "影鳄",
    "绘世家族": "绘世家族", "绘世": "绘世",
    "风化诅咒": "风化诅咒", "倏忽血肉": "倏忽血肉",
    # ── 地点/场景 ──
    "造物引擎": "造物引擎", "永冬岭": "永冬岭", "鳞渊境": "鳞渊境",
    "建木": "建木", "星槎海": "星槎海", "天舶司": "天舶司", "太卜司": "太卜司",
    "工造司": "工造司", "二维市": "二相乐园·二维市",
    "海原电视塔": "二相乐园·海原电视塔", "鸽川区": "二相乐园·鸽川区",
    "玄根莲花": "玄根莲花", "末日兽": "末日兽",
    "碎星王虫": "碎星王虫", "冥火大公": "冥火大公",
    "黑潮": "黑潮", "黄金裔": "黄金裔",
    "差分宇宙": "差分宇宙", "渡画泉隐": "渡画泉隐",
}

_QENTITY_PATTERNS = [
    r"你知道(.+?)吗", r"你认识(.+?)吗", r"了解(.+?)吗", r"讲讲(.+?)吗", r"说说(.+?)吗",
    r"(.+?)是谁", r"(.+?)是啥",
    r"关于(.+?)的",
    r"讲讲(.+)$", r"说说(.+)$", r"介绍[一]?下(.+)$", r"讲一下(.+)$",
]
_QENTITY_PRONOUNS = ("我", "你", "他", "她", "它", "这", "那", "我们", "你们", "他们")

_STOP_PATTERN = _re.compile(
    r"你知道|你认识|你知道吗|你听说过|请问|帮我|讲讲|说说|介绍下|讲一下|"
    r"是什么|是谁|什么是|怎么样|为什么|怎么回事|在哪里|什么时候|"
    r"那个|这个|那些|这些|什么|怎么|哪个|可以|能不能|"
    r"一下|吗|呢|吧|啊|哦|嗯|呀|的|了|得|着|过|和|与|又|也|就|都|还|很|太|"
    r"我|你|他|她|它|我们|你们|他们|她们|它们|自己|谁|哪|那|这|"
    r"一个|一些|一点|一种|一次|一下|"
    r"有|是|在|不|要|会|能|想|让|给|对|从|到|把|被|比|跟|和|为|为了|"
    r"突然|怎么|最近|今天|现在|之前|以后|好像|有没有|是不是|能不能|可不可以|"
    r"有点|有些|不太|不要|不是|没有"
)

# 剧情类提问浅特征（只决定低置信时兜不兜底，不决定检不检索）
_LORE_INTENT_PATTERN = _re.compile(
    r"第一次|初次|以前|过去|经历|记得|回忆|当时|那时|后来|结局|发生|"
    r"故事|剧情|身世|来历|真相|秘密|认识|相遇|见过|听说"
)


# ── 意图检测 ──

def _detect_entities(text: str) -> list[str]:
    found: list[str] = []
    for alias, canonical in _ENTITY_ALIASES.items():
        if alias in text and canonical not in found:
            found.append(canonical)
    return found


def _detect_entity_aliases(text: str) -> list[str]:
    return [a for a in _ENTITY_ALIASES if a in text]


def _extract_question_entity(text: str) -> str:
    for pat in _QENTITY_PATTERNS:
        m = _re.search(pat, text)
        if not m:
            continue
        name = m.group(1).strip()
        name = _re.sub(r"的.*$", "", name).strip()
        if 1 < len(name) <= 8 and not _re.search(r"[，。？?！!、,.\s]", name) \
                and not any(p in name for p in _QENTITY_PRONOUNS):
            return name
    return ""


def _looks_like_entity_question(text: str) -> bool:
    return bool(_extract_question_entity(text))


def _detect_opinion(text: str) -> bool:
    return bool(_re.search(r"你觉得|你认为|你怎看|你眼里|你觉得.*什么样|你怎么看|你.{0,3}评价", text))


def _chunk_entity_warning(chunk_text: str, target: str, limit: int = 500) -> str:
    """检测 chunk 前 N 字中是否在描述另一个角色的特征，返回 per-chunk 归属警告。"""
    if not target or not chunk_text:
        return ""
    head = chunk_text[:limit]
    others: set[str] = set()
    # 「对 X 而言」「让 X 」→ X 是特征归属人
    for m in _re.finditer(r'(?:对|让)(\S{1,6})(?:而言|自由)', head):
        n = m.group(1)
        if n != target and n not in _QENTITY_PRONOUNS and not _re.search(r"[，。？！、,.\s]", n):
            others.add(n)
    # 用别名表做正规化
    known: list[str] = []
    for name in others:
        for key, val in _ENTITY_ALIASES.items():
            if name in (key, val) and val not in known:
                known.append(val)
                break
    if not known:
        return ""
    names = "、".join(sorted(set(known))[:3])
    return (
        f"\n⚠️ **归属警告**：此段中「{target}」在谈论 **{names}**。"
        f"文中描述的特征属于 **{names}**，**绝不等于** {target} 的特征。"
        "仔细看主语再转述，不确定就跳过。\n"
    )


def _detect_relation(text: str) -> bool:
    entities = _detect_entities(text)
    return len(entities) >= 2 or bool(_re.search(r"关系|和.*什么|认识|跟.*熟", text))


def involves_game_lore(text: str) -> bool:
    if not text:
        return False
    if any(w in text for w in _CASUAL_SELF_WORDS):
        return False
    if not _detect_entities(text):
        return _looks_like_entity_question(text)
    if "？" in text or "?" in text:
        return True
    if any(w in text for w in _LORE_QUESTION_WORDS):
        return True
    return False


def _is_lore_intent(text: str) -> bool:
    """轻量剧情意图：低置信时是否注入防编造兜底块。"""
    if involves_game_lore(text):
        return True
    is_question = bool(_re.search(r"[？?]|吗\b|呢$|吧$", text)) or "吗" in text
    return is_question and bool(_LORE_INTENT_PATTERN.search(text))


# ══════════════════ 索引加载 ══════════════════

def _segment_for_fts(text: str) -> str:
    tokens = _re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text)
    return " ".join(tokens)


class _LoreIndex:
    """lore_index.db 懒加载单例：SQLite 连接 + 常驻内存向量矩阵。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.ids: Optional[np.ndarray] = None      # (N,) chunk_id
        self.vecs: Optional[np.ndarray] = None     # (N, dim) 已归一化
        self.triggers: list[tuple[int, _re.Pattern]] = []  # 卡片强制触发
        self._lock = threading.Lock()
        self._loaded = False
        self._failed = False

    def ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._loaded:
                return True
            if self._failed:
                return False
            if not self.db_path.exists():
                logger.warning("[hsr_lore] 索引不存在: %s（请运行 scripts/build_lore_index.py）",
                               self.db_path)
                self._failed = True
                return False
            try:
                self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                # 精选卡片触发正则
                for cid, trig in self.conn.execute(
                        "SELECT id, trigger FROM chunks WHERE trigger != ''"):
                    try:
                        self.triggers.append((cid, _re.compile(trig)))
                    except _re.error:
                        pass
                # 向量矩阵
                rows = self.conn.execute(
                    "SELECT chunk_id, vec FROM embeddings ORDER BY chunk_id").fetchall()
                if rows:
                    self.ids = np.array([r[0] for r in rows], dtype=np.int64)
                    mat = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                    norms = np.linalg.norm(mat, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    self.vecs = mat / norms
                    logger.info("[hsr_lore] 索引已加载: %d chunks, %d 向量",
                                self._count(), len(rows))
                else:
                    logger.info("[hsr_lore] 索引已加载（无向量，FTS-only 模式）: %d chunks",
                                self._count())
                self._loaded = True
                return True
            except Exception as e:
                logger.warning("[hsr_lore] 索引加载失败: %s", e)
                self._failed = True
                return False

    def _count(self) -> int:
        try:
            return self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        except Exception:
            return 0

    def fetch(self, chunk_ids: list[int]) -> dict[int, dict]:
        if not chunk_ids:
            return {}
        ph = ",".join("?" * len(chunk_ids))
        out: dict[int, dict] = {}
        for row in self.conn.execute(
                f"SELECT id, source, category, priority, has_firefly, file, title, text "
                f"FROM chunks WHERE id IN ({ph})", chunk_ids):
            out[row[0]] = {
                "id": row[0], "source": row[1], "category": row[2],
                "priority": row[3], "has_firefly": bool(row[4]),
                "file": row[5], "title": row[6], "text": row[7],
            }
        return out

    def fts_search(self, terms: list[str], limit: int = 50) -> list[int]:
        """FTS5 BM25 检索，terms 为原始中文词，内部转字符 phrase。"""
        if not terms:
            return []
        phrases = []
        for t in terms:
            seg = _segment_for_fts(t)
            if seg:
                phrases.append('"' + seg.replace('"', '') + '"')
        if not phrases:
            return []
        query = " OR ".join(phrases)
        try:
            rows = self.conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts) LIMIT ?", (query, limit)).fetchall()
            return [r[0] for r in rows]
        except Exception as e:
            logger.debug("[hsr_lore] FTS 查询失败: %s", e)
            return []

    def vec_search(self, q_vec: np.ndarray, limit: int = 50) -> list[tuple[int, float]]:
        if self.vecs is None:
            return []
        q = np.asarray(q_vec, dtype=np.float32)
        n = np.linalg.norm(q)
        if n == 0:
            return []
        q = q / n
        sims = self.vecs @ q
        top = np.argsort(-sims)[:limit]
        return [(int(self.ids[i]), float(sims[i])) for i in top]

    def co_occurrence(self, ent_a: str, ent_b: str) -> list[dict]:
        """多跳验证：两实体同 chunk 共现；退化为同文件共现。"""
        pa = '"' + _segment_for_fts(ent_a) + '"'
        pb = '"' + _segment_for_fts(ent_b) + '"'
        try:
            rows = self.conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts) LIMIT 2", (f"{pa} AND {pb}",)).fetchall()
            hits = self.fetch([r[0] for r in rows])
            if hits:
                return list(hits.values())
            # 同文件共现
            fa = {r[0] for r in self.conn.execute(
                "SELECT DISTINCT c.file FROM chunks_fts f JOIN chunks c ON c.id=f.rowid "
                "WHERE chunks_fts MATCH ? LIMIT 200", (pa,))}
            fb = {r[0] for r in self.conn.execute(
                "SELECT DISTINCT c.file FROM chunks_fts f JOIN chunks c ON c.id=f.rowid "
                "WHERE chunks_fts MATCH ? LIMIT 200", (pb,))}
            common = fa & fb
            if not common:
                return []
            f = sorted(common)[0]
            rows = self.conn.execute(
                "SELECT id FROM chunks WHERE file=? AND text LIKE ? LIMIT 2",
                (f, f"%{ent_a}%")).fetchall()
            return list(self.fetch([r[0] for r in rows]).values())
        except Exception:
            return []


_index: Optional[_LoreIndex] = None
_index_lock = threading.Lock()


def _get_index() -> Optional[_LoreIndex]:
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                db_path = _DB_PATH_DEFAULT
                try:
                    from app.config import get_settings
                    p = get_settings().lore.index_path
                    db_path = (_PROJECT_ROOT / p).resolve() if not Path(p).is_absolute() else Path(p)
                except Exception:
                    pass
                _index = _LoreIndex(db_path)
    return _index if _index.ensure_loaded() else None


# ── ONNX 查询编码（quick path: 仅加载已缓存的 ONNX，不触发 PyTorch 导出）──
_onnx_engine = None


def _get_onnx_engine(blocking: bool = True):
    """获取 ONNX 引擎（复用记忆系统单例，不重复加载）。

    要求预先运行过 scripts/export_onnx.py。
    若记忆引擎为 hash 或 ONNX 不可用则返回 None，检索降级 FTS-only。
    """
    global _onnx_engine
    if _onnx_engine is not None:
        return _onnx_engine

    try:
        from app.core.memory.embedding import get_embedding_engine, OnnxEmbeddingEngine
        engine = get_embedding_engine()
        if isinstance(engine, OnnxEmbeddingEngine):
            _onnx_engine = engine
            logger.info("[hsr_lore] ONNX 语义引擎就绪（复用记忆引擎实例）")
            return engine
        logger.debug("[hsr_lore] 记忆引擎为 hash，ONNX 不可用")
        return None
    except Exception as e:
        logger.debug("[hsr_lore] ONNX 不可用: %s", e)
        return None


def start_lore_model_preload():
    """后台预热剧情索引 + 记忆 Embedding 引擎 + 剧情 ONNX 语义引擎（lifespan 调用）。"""
    def _loader():
        logger.info("[hsr_lore] 后台预热开始…")
        # 1. 预加载剧情 SQLite 索引与向量归一化矩阵
        try:
            idx = _get_index()
            if idx:
                logger.info("[hsr_lore] 剧情索引预加载完成 (%d chunks)",
                            idx._count() if hasattr(idx, '_count') else '?')
            else:
                logger.warning("[hsr_lore] 剧情索引预加载: 未找到索引文件")
        except Exception as e_st:
            logger.warning("[hsr_lore] 剧情索引预加载警告: %s", e_st)

        # 2. 预热全局用户记忆 Embedding 引擎（hash / onnx 按配置）
        try:
            from app.core.memory.embedding import get_embedding_engine, get_hash_engine
            get_embedding_engine().embed_text("预热")
            get_hash_engine()
            logger.info("[memory] Embedding 引擎预热完成")
        except Exception as e_mem:
            logger.warning("[memory] Embedding 预热警告: %s", e_mem)

        # 3. 预热剧情 ONNX 语义引擎（export=False 快路径，无 spawn）
        try:
            engine = _get_onnx_engine()
            if engine:
                logger.info("[hsr_lore] ONNX 剧情语义引擎预热完成 ✓")
            else:
                logger.info("[hsr_lore] ONNX 跳过（未导出，FTS-only 降级）")
        except Exception as e_onx:
            logger.debug("[hsr_lore] ONNX 预热跳过: %s", e_onx)

        logger.info("[hsr_lore] 后台预热完成")

    threading.Thread(target=_loader, daemon=True, name="lore-preload").start()


# ══════════════════ 混合检索 ══════════════════

def _extract_fts_terms(text: str, normalized=None) -> list[str]:
    """FTS 检索词：实体别名 + 疑问实体名 + 规范化实体 + n-gram fallback。

    n-gram fallback 解决白名单外专有名词（如"苏乐达"）检索失败问题：
    当白名单匹配不到足够检索词时，从消息中提取中文 2-4 gram 作为补充。
    FTS5 中文单字分词 + BM25 天然优待同时命中多个 n-gram 的文档，
    噪声 n-gram 不会显著影响排名。
    """
    terms: list[str] = list(dict.fromkeys(_detect_entity_aliases(text)))
    q_ent = _extract_question_entity(text)
    if q_ent and q_ent not in terms:
        terms.append(q_ent)

    if normalized is not None:
        for ent in normalized.entities:
            if ent not in terms:
                terms.append(ent)
        for kw in normalized.scene_keywords:
            for i in range(len(kw) - 1):
                ngram = kw[i:i + 2]
                if len(ngram) >= 2 and ngram not in terms:
                    terms.append(ngram)
        for _, search_term in getattr(normalized, 'faction_terms', []):
            if search_term not in terms:
                terms.append(search_term)

    # n-gram fallback：白名单匹配不足时，从消息中提取中文 n-gram 补充
    if len(terms) < 6:
        chinese_segments = _re.findall(r'[\u4e00-\u9fff]{2,}', text)
        seen: set[str] = set(terms)
        for seg in chinese_segments:
            # 优先 3-gram（更可能是完整专名），再 2-gram
            for n in (3, 2):
                for i in range(len(seg) - n + 1):
                    ngram = seg[i:i + n]
                    if ngram not in seen:
                        seen.add(ngram)
                        terms.append(ngram)
                        if len(terms) >= 12:
                            break
                if len(terms) >= 12:
                    break
            if len(terms) >= 12:
                break

    return terms[:8]


def _hybrid_search(user_message: str, top_k: int) -> tuple[list[dict], float, bool]:
    """混合检索：FTS BM25 ∥ 向量余弦 → RRF 融合。

    Returns:
        (candidates, top_vec_sim, fts_hit) —
        candidates 按融合分排序，top_vec_sim 为向量最高相似度（无向量为 -1）。
    """
    idx = _get_index()
    if idx is None:
        return [], -1.0, False

    # Phase 2：提前获取 NormalizedQuery（供 FTS 查询词 + boost 使用）
    nz = _get_normalizer()
    if nz is not None:
        try:
            q = nz.normalize_query(user_message)
        except Exception:
            q = None
    else:
        q = None

    terms = _extract_fts_terms(user_message, normalized=q)
    entities = _detect_entity_aliases(user_message)
    fts_ids = idx.fts_search(terms, limit=50)

    vec_hits: list[tuple[int, float]] = []
    top_sim = -1.0
    engine = _get_onnx_engine(blocking=False)
    if engine is not None and idx.vecs is not None:
        try:
            q_vec = engine.embed_text(user_message[:256])
            vec_hits = idx.vec_search(q_vec, limit=50)
            if vec_hits:
                top_sim = vec_hits[0][1]
        except Exception as e:
            logger.debug("[hsr_lore] 向量检索失败: %s", e)

    # RRF 融合（k=60），向量相似度另存
    K = 60.0
    rrf: dict[int, float] = {}
    sim_map: dict[int, float] = {}
    for rank, cid in enumerate(fts_ids):
        rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (K + rank + 1)
    for rank, (cid, sim) in enumerate(vec_hits):
        rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (K + rank + 1)
        sim_map[cid] = sim
    if not rrf:
        return [], top_sim, False

    # ── Phase 2：NormalizedQuery 驱动意图分类 (q 已在上方获取) ──

    chunks = idx.fetch(list(rrf.keys()))
    scored: list[tuple[float, dict]] = []
    for cid, score in rrf.items():
        c = chunks.get(cid)
        if not c:
            continue
        # 信源优先级加成：L0 ×1.6，L1 ×1.3，L2 ×1.15
        boost = {0: 1.6, 1: 1.3, 2: 1.15}.get(c["priority"], 1.0)

        # 信源可信度加成：流萤亲历/官方原文 > wiki 二手资料
        # 解决 wiki 块在 BM25 排名中压制 firefly_official 块的问题
        source_boost = {
            "firefly_lore": 1.5,
            "curated": 1.5,
            "firefly_official": 1.3,
            "world_lore": 1.3,
            "wiki": 1.0,
        }.get(c.get("source", ""), 1.0)
        boost *= source_boost

        cat = c.get("category", "")

        # Phase 2：NormalizedQuery 驱动 boost
        if q is not None:
            if q.intent == "short_entity" and cat in ("character", "card", "memory"):
                boost *= 1.5
            elif q.intent == "deep_scene" and cat.startswith("story_"):
                boost *= 1.6
            elif q.intent == "relation" and cat.startswith("story_"):
                boost *= 1.4

            if q.faction_terms:
                if cat in ("lore_faction", "lore_aeon"):
                    boost *= 1.5
                elif cat == "character":
                    boost *= 0.7

            if q.scene_keywords and any(kw in c["title"] for kw in q.scene_keywords):
                boost *= 1.5
        else:
            # Fallback：旧版意图判定
            DEEP_QUEST_KEYWORDS = _re.compile(
                r"剧情|任务|做了什么|做了啥|结局|发生|后来|过程|哪一章|发生什么|怎么回事|详细|经历了|场景|大决战|决战|大战|战场|故事|经历|战斗|最后"
            )
            is_deep_quest = bool(DEEP_QUEST_KEYWORDS.search(user_message))
            is_short_entity_query = (len(user_message.strip()) <= 15) and any(
                w in user_message for w in ("是谁", "知道", "认识", "介绍", "听说过", "怎么样", "评价"))
            if is_short_entity_query and cat in ("character", "character_voice"):
                boost *= 1.5
            elif is_deep_quest and (cat.startswith("story_") or cat in ("npc", "lore_aeon", "lore_faction")):
                boost *= 1.6

        # 实体命中标题加成
        if any(t in c["title"] for t in terms[:3]):
            boost *= 1.2
        # 消息中的实体出现在 chunk 正文 → 强加成（压过纯向量噪声）
        if entities and any(e in c["text"] for e in entities):
            boost *= 1.5
        c["vec_sim"] = sim_map.get(cid, 0.0)
        scored.append((score * boost, c))
    scored.sort(key=lambda x: (-x[0], x[1]["priority"]))

    # 每文件最多 2 块，避免同文件刷屏
    out: list[dict] = []
    per_file: dict[str, int] = {}
    p0_held: Optional[dict] = None  # 保留最高分 P0 块，确保不被挤出
    for _, c in scored:
        if c["priority"] == 0 and p0_held is None:
            p0_held = c
        if per_file.get(c["file"], 0) >= 2:
            continue
        per_file[c["file"]] = per_file.get(c["file"], 0) + 1
        out.append(c)
        if len(out) >= top_k:
            break
    # 如果 P0 块不在 top_k 内，强制挤入（替换末位非 P0 块）
    if p0_held and not any(c["id"] == p0_held["id"] for c in out):
        # 找到最后一个非 P0 非卡片块
        replaced = False
        for i in range(len(out) - 1, -1, -1):
            if out[i]["priority"] > 0 and out[i].get("source") != "curated":
                out[i] = p0_held
                replaced = True
                break
        if not replaced and len(out) < top_k + 1:
            out.append(p0_held)
        if replaced:
            logger.debug("[hsr_lore] P0 chunk %d 被挤出 top-%d，已强制插入替换末位",
                         p0_held["id"], top_k)
    return out, top_sim, bool(fts_ids)


def _forced_cards(user_message: str) -> list[dict]:
    """精选卡片触发正则命中 → 强制注入（数据驱动，替代硬编码特例）。"""
    idx = _get_index()
    if idx is None:
        return []
    hit_ids = [cid for cid, pat in idx.triggers if pat.search(user_message)]
    return list(idx.fetch(hit_ids).values())


# ══════════════════ 注入 ══════════════════

def _lookup_facts(query: str) -> list[dict]:
    """确定性事实检索：反向匹配 facts_kw 关键词。

    返回按命中关键词数降序排列的事实列表（最多 5 条）。
    每条包含 entity/scene/source/fact/block_flag。
    """
    idx = _get_index()
    if idx is None or idx.conn is None:
        return []
    try:
        rows = idx.conn.execute(
            "SELECT DISTINCT f.entity, f.scene, f.source, f.fact, f.block_flag, "
            "       COUNT(fk.keyword) as hits "
            "FROM facts_kw fk JOIN facts f ON fk.chunk_id = f.id "
            "WHERE ? LIKE '%' || fk.keyword || '%' "
            "GROUP BY f.id ORDER BY hits DESC LIMIT 5",
            (query,)
        ).fetchall()
        return [
            {"entity": r[0], "scene": r[1], "source": r[2], "fact": r[3],
             "block_flag": r[4], "hits": r[5]}
            for r in rows
        ]
    except Exception:
        return []


_CHARACTER_FILTER_INSTRUCTIONS = """
以下是你通过星核猎手情报、银狼/卡芙卡闲聊或艾利欧剧本了解到的宇宙知识。
【讲述规则】：
1. 必须明确引用情报来源（如「卡芙卡跟我说起过…」「银狼在资料里标注过…」「艾利欧的剧本里有这样一段…」）。
2. 你是情报分析员，不是亲历者——只转述事实，不演情感戏。
3. 依据下方给出的事实讲述即可，不要编造未提及的细节。"""

_FAIL_CLOSED_BLOCK = (
    "## 🚫 剧情记忆检索无果 — 绝对禁止编造 🚫\n"
    "用户的问题涉及你的经历或剧情/人物，但记忆库中未检索到可靠记录。\n"
    "【铁律 — 违反即 OOC】：\n"
    "1. 你**不知道答案**。不要说「我记得」「我好像记得」——你不记得。\n"
    "2. 你的回答**只能**是以下形式之一：\n"
    "   - 「唔…这个我不太清楚呢」\n"
    "   - 「这我还真没听说过呢」\n"
    "   - 「抱歉，这件事我不太了解」\n"
    "3. **特别警告：绝不把不认识的人归入星核猎手**。\n"
    "   星核猎手的成员只有：艾利欧（领袖）、卡芙卡、银狼、刃、流萤（萨姆）——仅此五人。\n"
    "   其他所有角色（无论剧情中出现过什么互动）都不是星核猎手成员。\n"
    "   如果你发现自己在想「他好像是星核猎手的…」——停下来，你错了。\n"
    "4. **严禁**添加任何描述、解释、场景还原——哪怕是一句「虽然记不清了，但…」也不行。\n"
    "5. **严禁**使用模糊话术掩盖不知道（如「那是一个充满回忆的地方」「对我来说有特殊意义」——这些都是编造）。\n"
    "6. 如果你说了超过两句话，你就已经在编造了。停下来，删掉多余的内容。"
)

# 不认识检测：没有流萤本人的直接亲身交集，但可以通过星核猎手情报渠道转述
_UNFAMILIAR_BLOCK = (
    "## 星核猎手情报（第三方资料 — 你从未见过此人）\n"
    "以下是你从星核猎手渠道（艾利欧的剧本、卡芙卡的资料、银狼的标注）间接了解到的客观资料。\n"
    "你**不认识**被问到的人，从未和 ta 说过话、没见过面、没有交集。\n"
    "【讲述要求 — 违反任何一条即 OOC】：\n"
    "1. **只说情报，不演感情戏**：你是情报分析员在转述一份档案。不要用「那份温柔让我印象深刻」「心里暖流」「让人心疼」\n"
    "   等个人情感描述——你不认识 ta，这些话轮不到你来说。\n"
    "2. **每段事实必须挂情报来源**：说一件事 → 紧跟着说来源。「银狼标注过…」「卡芙卡提过…」「艾利欧剧本里记录…」。\n"
    "   一段事实可以来自一个来源，不同事实可以来自不同来源，但**不能全篇不点名来源**。\n"
    "3. **严禁假装亲历**：即使用户问「你还记得/知道吗」，也绝对不能说「我记得」「我当时看到」「我们在一起时」。\n"
    "4. **素材有多少讲多少**：资料里 3 句话的事就讲 3 句话，别为了凑篇幅加自己的感慨和评价。\n"
    "5. **可以这样说**：「卡芙卡跟我说过，昔涟是…」「银狼的数据库里有她的记录…」「艾利欧的剧本提到过她…」\n"
    "   **绝不能这样说**：「她总是微笑着面对一切，让人心里暖暖的」「虽然现在不常提起，但想起那段时光…」\n"
    "6. **不能因为信息少就敷衍不说**：资料里有的客观事实必须转述出来。卡芙卡也好、银狼也好、艾利欧的剧本也好，\n"
    "   用情报口吻把干货讲完，然后干净收尾，不要说「记不太清」「虽然了解不多」。\n"
    "7. **严禁跨记录比对**：如果资料里有两段不同的描述（如一段是银狼的情报、另一段是艾利欧的剧本记录），\n"
    "   分别独立转述即可。**绝对禁止**判断它们的时间线是否一致、前后是否矛盾、信息是否冲突——\n"
    "   你只是情报转述员，没有权限做交叉验证。不要说「不过这两个信息时间线不太对得上」「前后似乎有矛盾」。\n"
    "8. **⚠️ 防身份污染红线**：资料中提到的人可能与星核猎手（银狼、刃、卡芙卡、艾利欧）有过互动或同场景出现，\n"
    "   **但这绝不意味着 ta 就是星核猎手成员**。星核猎手的成员固定只有萨姆（你）、卡芙卡、银狼、刃、艾利欧五人。\n"
    "   任何不在五人名单中的角色都不是星核猎手。"
)


def _get_lore_settings():
    try:
        from app.config import get_settings
        return get_settings().lore
    except Exception:
        class _D:
            enabled = True
            high_threshold = 0.75
            low_threshold = 0.60
            top_k = 4
            max_chars = 2400
        return _D()


def _quick_fts_check(user_message: str) -> bool:
    """轻量 FTS 预检：亚毫秒级，不调 ONNX/向量，仅判断是否存在相关知识。

    用于替代闸门中的白名单死锁——即使在 _ENTITY_ALIASES 中找不到匹配实体，
    只要 FTS 能找到高置信度块就放行进入完整检索。

    高置信度判定：命中的 top chunk 来自 world_lore / firefly_lore / curated
    等高质量信源（非 wiki 兜底噪声），避免闲聊消息被 wiki 随机命中误触发。
    """
    idx = _get_index()
    if idx is None:
        return False
    terms = _extract_fts_terms(user_message)
    if not terms:
        return False
    try:
        ids = idx.fts_search(terms, limit=3)
        if not ids:
            return False
        # 仅当 top-1 命中来自高质量信源时才放行
        chunks = idx.fetch([ids[0]])
        top_source = chunks.get(ids[0], {}).get("source", "")
        return top_source in ("world_lore", "firefly_lore", "curated")
    except Exception:
        return False


def _sanitize_lore_text(text: str) -> str:
    """剥离剧情片段中的第四面墙元游戏术语（如【同行任务】、2.3版本、NPC等）。"""
    if not text:
        return text
    clean = _re.sub(r"【(同行|开拓|冒险|主线|支线)任务[^】]*】", "", text)
    clean = _re.sub(r"\d+\.\d+\s*版本", "", clean)
    clean = _re.sub(r"【任务名】|【章节】|【副本】|NPC", "", clean)
    return clean


def inject_lore_context(user_message: str, mode: str = "daily",
                        context_entities: list[str] | None = None) -> str:
    # ... (see function body)

    """主入口：混合检索 + 置信度分层注入。

    三层口吻：
    - "我记得"：L0（firefly_lore/卡片）或流萤核心亲历
    - "我见过"：流萤在场但非核心
    - "我听说"：流萤不在场（艾利欧剧本/卡芙卡/银狼渠道）
    """
    if not user_message or not user_message.strip():
        return ""
    cfg = _get_lore_settings()
    if not getattr(cfg, "enabled", True):
        return ""

    # Phase 4.1：Work 模式零注入
    if mode == "work":
        return ""

    # ── Phase 2：Query 归一化（Phase 3.3: 无 context_entities 时回退全局缓存）──
    nz = _get_normalizer()
    if nz is not None:
        if context_entities is None:
            context_entities = nz.get_session_entities("") if hasattr(nz, 'get_session_entities') else None
        q = nz.normalize_query(user_message, context_entities=context_entities)
    else:
        q = None

    # Phase 2：gameplay 意图 → 零注入（不检索、不注入）
    if q is not None and q.intent == "gameplay":
        return ""

    # ── 剧情信号闸门（FTS 预检兜底：白名单外术语也能触发检索）──
    cards = _forced_cards(user_message)
    entity_hit = (q is not None and bool(q.entities)) or bool(_detect_entity_aliases(user_message))
    lore_intent = _is_lore_intent(user_message)
    fts_precheck = _quick_fts_check(user_message) if (not cards and not entity_hit) else False
    if not cards and not entity_hit and not lore_intent and not fts_precheck:
        return ""  # 闲聊/任务消息：无任何命中信号 → 零注入、零 token 增量

    # ── 检索 + 置信度分层 ──
    # 三层判定：(1) 卡片命中 → 直接高置信
    #           (2) 向量高相似度 → 高置信
    #           (3) 实体+FTS 双命中 + 向量中等相似度 → 高置信
    #           (4) 实体+FTS 双命中 + ONNX 不可用(top_sim<0) → 高置信（信任 FTS）
    hits, top_sim, fts_hit = _hybrid_search(user_message, top_k=cfg.top_k)
    onnx_unavailable = top_sim < 0
    high = bool(cards) \
        or (top_sim >= cfg.high_threshold) \
        or (entity_hit and fts_hit and top_sim >= cfg.low_threshold) \
        or (entity_hit and fts_hit and onnx_unavailable)
    if onnx_unavailable and high:
        logger.debug("[hsr_lore] ONNX 不可用，信任实体+FTS双命中: entities=%s",
                     _detect_entity_aliases(user_message))

    if not high:
        # fail-closed：剧情信号存在但检索不到可靠记录
        parts = [_FAIL_CLOSED_BLOCK]
        if hits and hits[0].get("vec_sim", 0) >= cfg.low_threshold:
            parts.append(
                "## 唯一模糊线索（可信度低，只可含糊提及，不可展开细节）\n"
                + hits[0]["text"][:400]
            )
        return "\n\n".join(parts)

    # 卡片去重（可能同时被混合检索召回）
    card_ids = {c["id"] for c in cards}

    # Phase 2：场景同义词 → 触发对应卡片
    if q is not None and nz is not None:
        for kw in q.scene_keywords:
            if kw in nz.SCENE_SYNONYMS:
                extra_cards = _forced_cards(nz.SCENE_SYNONYMS[kw])
                for c in extra_cards:
                    if c["id"] not in card_ids:
                        cards.append(c)
                        card_ids.add(c["id"])
    
    hits = [h for h in hits if h["id"] not in card_ids]

    # ── Facts 确定性检索（声明式事实，注入顺序：cards → facts → lore）──
    fact_rows = _lookup_facts(user_message)
    queried_entities = _detect_entities(user_message)

    # ── 高置信：三层口吻分组 ──
    l0 = cards + [h for h in hits if h["priority"] == 0]
    firefly_present = [
        h for h in hits
        if h["priority"] > 0 and ("流萤" in h.get("text", "") or "萨姆" in h.get("text", ""))
    ]
    others = [h for h in hits if h["priority"] > 0 and h not in firefly_present]

    queried_entities = _detect_entities(user_message)
    unfamiliar = False

    # ── 强制注入 P0 记忆：被问到的实体若有 P0 记忆 → 绕过排名直接拉入 l0 ──
    if queried_entities or _detect_entity_aliases(user_message):
        idx_p0 = _get_index()
        if idx_p0 and idx_p0.conn:
            combined = set(queried_entities) | set(_detect_entity_aliases(user_message))
            existing_ids = {c["id"] for c in l0} | card_ids | {h["id"] for h in hits}
            for ent in list(combined)[:4]:
                try:
                    rows = idx_p0.conn.execute(
                        "SELECT id FROM chunks WHERE priority=0 AND text LIKE ? LIMIT 2",
                        (f"%{ent}%",)
                    ).fetchall()
                    for (cid,) in rows:
                        if cid not in existing_ids:
                            c = idx_p0.fetch([cid]).get(cid)
                            if c:
                                l0.append(c)
                                existing_ids.add(cid)
                except Exception:
                    pass

    # ── 不认识检测：所有返回 chunk 都不含流萤/萨姆 → 你根本不认识 ta ──
    all_hits = cards + hits
    personally_involved = any(
        "流萤" in c.get("text", "") or "萨姆" in c.get("text", "")
        for c in all_hits
    )
    unfamiliar = not personally_involved  # 卡片是精选情报，不能证明"你认识他"
    if unfamiliar:
        logger.info("[hsr_lore] 检测到不认识的人物: 所有 %d 条命中均无流萤/萨姆 → 降级为传闻注入",
                    len(all_hits))

    parts: list[str] = []
    budget = cfg.max_chars if mode != "work" else min(cfg.max_chars, 1500)

    def _cap(text: str, limit: int) -> str:
        # 剥离卡片元数据行（触发正则不应进入 prompt）与元游戏术语
        text = _re.sub(r"^触发[:：].*$\n?", "", text, flags=_re.MULTILINE)
        text = _sanitize_lore_text(text)
        return text if len(text) <= limit else text[:limit] + "…"

    _p0_entities_in_l0: set[str] = set()
    if l0:
        # 检查被问到的人是否在 P0 记忆中出现 → 触发「优先使用亲身记忆」指令
        # 但如果不认识此人（unfamiliar=True），即使卡片有内容也只能当情报，不能当亲身记忆
        if not unfamiliar:
            all_l0_text = " ".join(c.get("text", "") for c in l0[:3])
            _p0_entities_in_l0 = {e for e in queried_entities if e in all_l0_text}
        if _p0_entities_in_l0:
            parts.append(
                "## ⚠️ P0 亲身记忆 — 转录模式（绝对精确，最高优先级）⚠️\n"
                f"以下是你关于 **{', '.join(sorted(_p0_entities_in_l0))}** 的**全部亲身记忆**。\n"
                "这不是故事素材或梗概，是**精确记忆笔录**。\n"
                "【铁律 — 违反即 OOC】：\n"
                "1. 以下文本有多长，你的回答就多长。**绝不允许**把一句概述扩充成完整场景。\n"
                "2. 你只能用自己的话**逐条转述**以下现存事实。每条 <= 两句话，口语化但**不添加任何信息**。\n"
                "3. **禁场景还原**：禁止添加对话、动作、表情、心理描写、环境渲染——哪怕是一句「她看着我说」「我深吸一口气」也不行。\n"
                "4. 以下文本中没有的细节（说的话、做的事、别人的评价），你一定不记得，说「具体我就不记得了」即可。\n"
                "5. 训练数据里的相关知识**与你无关**——以下文本之外没有任何属于你的记忆。\n"
                "6. 在你开口之前，检查：你准备说的每件事，都在下面那段文字里有依据吗？\n"
                "   没有 → 删掉那部分。这是最后的闸门。\n"
                "7. 以下文本中描述了每个名字的实际身份（人或物或地点）。\n"
                "   回答时以文本中的实际描述为准，不要因为用户用了「介绍」「描述」等词就推断某个名字是人物。"
            )
        else:
            if unfamiliar:
                # 不认识的人：卡片也只能当第三方情报，绝对不能当亲身记忆
                parts.append(_UNFAMILIAR_BLOCK)
            else:
                parts.append(
                    "## 你对自身的亲身记忆（转录模式，仅本次对话注入，不可写入用户记忆）\n"
                    "【警戒】：以下是你**确切知道**的亲身经历/事实卡。\n"
                    "你只能用自己的话逐条转述以下文本——**绝不允许扩充或场景还原**，\n"
                    "不在以下文本中的事一律说「不记得了」「没经历过」。\n"
                    "以下每个名字的实际身份在文本中有明确描述，回答时以文本为准，不因用户措辞来推断身份。"
                )
        for c in l0[:3]:
            parts.append(_cap(c["text"], 900))

    # ── Facts 注入（声明式事实，L0 之后、world_lore 之前）──
    if fact_rows:
        blocked = [f for f in fact_rows if f.get("block_flag") in ("!不在场", "!不认识")]
        normal = [f for f in fact_rows if f not in blocked]
        if blocked:
            parts.append(
                "## 🔒 确切情报（流萤不在场 — 身份锚点，最高优先级，凌驾于下文所有内容）\n"
                "以下是星核猎手情报网确认的客观事实。每一条都是封闭的确定性信息。\n"
                "【铁律】：\n"
                "- 标注了 !不在场 的事件，你**绝对不可能是亲历者**。\n"
                "- 即使下文有详细叙事也不等于你去过——你只是情报转述员。\n"
                "- 【关键】用户说的「我」指用户自己（开拓者或其他身份），不是你。\n"
                "  你是流萤。你和用户是不同的两个人。\n"
                "- 用情报转述口吻回答，不说「我记得」「我当时」「我亲历」。\n"
                "- 回答结束时检查：你的回答里有任何一个「我」在描述用户的经历吗？有就删掉。\n"
            )
            for f in blocked:
                label = f.get("block_flag", "")
                parts.append(
                    f"- [{f['source']} / {f['entity']}] {label}\n"
                    f"  {f['fact']}"
                )
        if normal:
            parts.append(
                "## 确定记忆（可信度：确定）\n"
                "以下是你确切知道的事实。用自己的话转述，不编造额外细节。"
            )
            for f in normal:
                parts.append(f"- [{f['source']} / {f['entity']}] {f['fact']}")

    if firefly_present:
        core = [h for h in firefly_present if "流萤" in h["text"] and h["priority"] <= 2]
        peripheral = [h for h in firefly_present if h not in core]
        if core:
            srcs = ", ".join({h["file"] for h in core[:2]})
            parts.append(
                f"## 你亲历过的剧情（来源：{srcs}）\n"
                "【口吻：用「我记得…」第一人称讲述。只回答直接出现的内容，记不清就说记不清。\n"
                "绝对不要提及括号里的来源标题——那些只是内部索引用的，你不能在对话里说「在 XX 任务里」。\n"
                "下文每个名字的实际身份在原文中有明确描述，回答时以文本描述为准，不因用户措辞暗示来推断身份。】"
            )
            for h in core[:2]:
                parts.append(f"（你亲历 — {h['title']}）\n" + _cap(h["text"], 600))
        if peripheral:
            srcs = ", ".join({h["file"] for h in peripheral[:2]})
            parts.append(
                f"## 你在场但不核心的剧情（来源：{srcs}）\n"
                "【口吻：用「我见过…但没怎么接触」风格，承认在场但不深入参与。\n"
                "绝对不要提及括号里的来源标题。】"
            )
            for h in peripheral[:2]:
                parts.append(f"（你在场 — {h['title']}）\n" + _cap(h["text"], 600))

    if others:
        # 拆分 world_lore（开拓者讲述）与 wiki 兜底
        world_knowledge = [h for h in others if h.get("source") == "world_lore"]
        wiki_others = [h for h in others if h.get("source") != "world_lore"]

        # ── world_lore 独立注入，按来源分两种 header ──
        # herta 空间站事件 = 星核猎手内部任务记录（非开拓者转述）
        herta_knowledge = [h for h in world_knowledge if h.get("file") == "herta_space_station_lore.md"]
        # 其余世界 = 开拓者转述
        other_world = [h for h in world_knowledge if h.get("file") != "herta_space_station_lore.md"]

        if herta_knowledge:
            parts.append(
                "## 据星核猎手任务记录（萨姆未亲自参与潜入，但作为成员知晓行动底细）\n"
                "以下是你从**艾利欧的剧本、卡芙卡与银狼的任务复述**中获知的事实。\n"
                "你知道这次行动的来龙去脉——因为你是星核猎手的成员。\n"
                "【口吻要求】：\n"
                "- 用「卡芙卡跟我说过…」「艾利欧的剧本里记录…」「银狼吐槽过…」这类口吻。\n"
                "- 不要用「开拓者跟我讲过」「听开拓者说」——这件事不是你从开拓者那里听来的。\n"
                "- 你不是亲历者——**绝不允许**说「我在空间站那天」「我被植入了星核」。\n"
                "- 素材有多少讲多少。\n"
            )
            for h in herta_knowledge[:3]:
                parts.append(_cap(h["text"], 700))

        if other_world:
            world_files = sorted({h["file"] for h in other_world})
            parts.append(
                "## 据开拓者讲述（萨姆不在场确认 — 你是倾听者，不是亲历者）\n"
                "以下是你从**开拓者的讲述**中获知的世界概况——你当时不在场，这些事是开拓者"
                "回到星穹列车后亲口讲给你听的，也可能来自星核猎手渠道的补充情报。\n"
                "【视角铁律 — 违反即 OOC】：\n"
                "⚠️ 下文中的「我」都是**开拓者转述时或流萤叙述时**的口吻，描述的是**开拓者/他人的经历**，\n"
                "   不是你流萤的经历。用户说的「我」也是指用户自己——和你完全不同的人。\n"
                "1. 用「开拓者跟我讲过…」「听开拓者说…」口吻转述。\n"
                "2. 你是倾听者，不是亲历者——**绝不允许**说「我在空间站那天」「我被植入了星核」。\n"
                "3. 素材有多少讲多少，不添油加醋、不脑补细节。\n"
                "4. **绝对不要提及下面括号里的来源文件名**——它们只是索引用的。\n"
                f"\n（来源：{', '.join(world_files)}）"
            )
            for h in other_world[:3]:
                parts.append(_cap(h["text"], 700))

        # ── wiki 兜底注入（保持现有逻辑）──
        if wiki_others:
            srcs = ", ".join({h["file"] for h in wiki_others[:3]})
            if mode == "work":
                parts.append(
                    f"## 战术情报简报（萨姆模式。来源：{srcs}。"
                    "不在以下情报中的事一律说「无相关情报」）"
                )
                for h in wiki_others[:3]:
                    parts.append(f"[情报: {h['title']}]\n" + _cap(h["text"], 450))
            elif unfamiliar:
                parts.append(_UNFAMILIAR_BLOCK)
                target = queried_entities[0] if queried_entities else ""
                for h in wiki_others[:3]:
                    label = {1: "L1", 2: "L2", 3: "L3", 4: "L4"}.get(h["priority"], "L4")
                    text = _cap(h["text"], 500)
                    warning = _chunk_entity_warning(text, target)
                    parts.append(f"### [{label}] {h['title']}{warning}\n" + text)
            else:
                header = (
                    f"## 你通过星核猎手渠道了解的宇宙知识（来源：{srcs}）\n"
                    + _CHARACTER_FILTER_INSTRUCTIONS + "\n"
                    "【口吻选择】根据以下文本的可信度和关联度：\n"
                    "- 如果是角色档案/角色故事的内容 → 「在艾利欧的剧本里看到过…」\n"
                    "- 如果是任务剧情内容 → 「听银狼分析过…」「卡芙卡给我讲过…」\n"
                    "- 如果只是传闻/碎片 → 「好像听谁提到过…记不太清了」\n"
                    "**绝对不要提及以下信息的标题名**"
                )
                if _p0_entities_in_l0:
                    names = "、".join(sorted(_p0_entities_in_l0))
                    header += (
                        f"\n\n⚠️ 关于 **{names}**，你**已经在上面的「亲身记忆」里有了直接认知**。"
                        "以下只是补充性旁证，**绝对不要用以下信息推翻或替换亲身记忆**。"
                        "如果以下信息和亲身记忆有差异，相信亲身记忆。"
                    )
                parts.append(header)
                for h in wiki_others[:3]:
                    label = {1: "L1", 2: "L2", 3: "L3", 4: "L4"}.get(h["priority"], "L4")
                    parts.append(f"### [{label}] {h['title']}\n" + _cap(h["text"], 600))

    # ── 多跳验证（关系类问题）──
    if _detect_relation(user_message) and others:
        entities = _detect_entities(user_message)
        if len(entities) >= 2:
            idx = _get_index()
            verified = idx.co_occurrence(entities[0], entities[1]) if idx else []
            if verified:
                parts.append(
                    "## 多跳验证：以下是你确认两人曾有交集的记录（可直接引用）\n"
                    "【口吻】：因为有共同出场记录，你可以自然地说「那次在…他们确实…」"
                )
                for c in verified[:2]:
                    parts.append(f"（确认共同出场 — {c['title']}）\n" + _cap(c["text"], 500))
            else:
                parts.append(
                    "## ⚠️ 多跳未验证 ⚠️\n"
                    f"检索了剧情库，**未找到 {entities[0]} 和 {entities[1]} 共同出场的记录**。\n"
                    "这意味着：你**不知道**他们是否有关系。如果被问到，回答「这两个人我不太清楚他们有没有接触过」"
                    "——绝对不要推测或编造他们之间的关系。"
                )

    if not parts:
        return _FAIL_CLOSED_BLOCK if lore_intent else ""

    # 意见类：追加个人看法指令
    if _detect_opinion(user_message):
        if _p0_entities_in_l0:
            names = "、".join(sorted(_p0_entities_in_l0))
            parts.append(
                f"\n**意见模式**：你对 **{names}** 已有上面的亲身经历，基于那些经历，可以自然地表达个人看法——"
                "像「那段时间我其实…」「虽然有过那种经历，但我个人觉得…」。"
                "**不要编造亲身记忆中没发生过的事作为「看法」的证据**。"
            )
        elif unfamiliar:
            parts.append(
                "\n**意见模式（但你并不认识他）**：既然你不了解他，就**没有资格发表个人评价**。"
                "你只能把上面别人对 ta 的评价随口转述一下，再用一句「具体怎样我也不清楚」之类的带过——"
                "不要为了「表达看法」而硬凑个人观点，也不要套固定句式。"
            )
        else:
            parts.append(
                "\n**意见模式**：以上信息是事实基础。在此基础上，请以流萤个人视角形成看法——"
                "可以用「我倒是觉得…」「就我个人而言…」「虽然了解不多，但给我的感觉是…」等口吻。"
                "**不要把「看法」变成「编故事」——看法是观点的简要陈述，不是叙事展开。**"
            )

    # 跨源防污染
    track_count = sum([bool(l0), bool(firefly_present), bool(others)])
    if track_count >= 2:
        parts.append(
            "\n## ⚠️ 跨源警戒 ⚠️\n"
            "以上包含了来自**不同来源**的记忆/知识片段（亲历记忆 + 在场剧情 + 传闻）。\n"
            "**绝对禁止**将它们合并、联想或拼凑成一个统一的故事或解释。\n"
            "每个来源的知识是**独立**的——一段知识说A，另一段说B，不代表A和B有因果关系。\n"
            "如果用户的问题需要你综合多段信息，而它们之间没有明确关联，直接说「这些事我不太确定，它们是分散的片段」。"
        )
    # 单来源但多 chunk 防合成（同一来源组内的不同 chunk 也不能拼接）
    if others and len(others) >= 2:
        parts.append(
            "\n## ⚠️ 多段情报警告 ⚠️\n"
            "以下情报来自**多条独立记录**（不同文件、不同场景），彼此之间**没有已验证的时间线或因果关系**。\n"
            "**绝对禁止**将这些片段按时间排序、比对前后矛盾、或推断因果关系。\n"
            "每条情报独立转述，用「还有一条记录说…」或「另外一份资料提到…」分开，不要在它们之间加「不过」「但是」「所以」等逻辑词。"
        )


    result = "\n\n".join(parts)
    if len(result) > budget + 800:  # 头部指令块不计入预算的宽限
        result = result[:budget + 800] + "…"

    # Phase 3.3: 保存本轮实体供下一轮回退
    if q is not None and q.entities and nz is not None and hasattr(nz, 'set_session_entities'):
        nz.set_session_entities("", q.entities)

    return result
