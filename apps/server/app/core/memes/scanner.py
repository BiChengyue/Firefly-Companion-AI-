"""表情包选择器 — 对应 spec 3.1（LLM 驱动方案）。

约定：
- 文件名格式: {emotion}[_{描述}].{后缀}，如 happy_excited.jpg / sad_sigh.gif
- emotion 必须是 7 种标签之一: happy / sad / angry / shy / thinking / surprised / neutral
- work_sam 是特殊标签，由 work 模式专属匹配

逻辑：
- LLM 在回复末尾打 [emo:xxx] → 后端解析 → 按 emotion 前缀索引随机选一张
- 同一 emotion 有冷却（默认间隔 2 条消息），避免刷屏
- 无匹配文件时不发送（不硬塞）
"""
import random
from pathlib import Path
from typing import Optional

from app.core import paths as _paths

BUILTIN_MEMES_DIR = _paths.BUILTIN_MEMES_DIR
USER_MEMES_DIR = _paths.USER_MEMES_DIR

_VALID_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".apng"}
VALID_EMOTIONS = frozenset({"happy", "sad", "angry", "shy", "thinking", "surprised", "neutral", "work"})

# 表情描述词 → 语义匹配触发关键词映射
_SEMANTIC_KEYWORDS: dict[str, list[str]] = {
    "cute": ["气鼓鼓", "哼", "锤你", "小拳拳", "傲娇", "坏人", "醋", "吃醋"],
    "no": ["不行", "不可以", "不要", "不准", "拒绝", "绝不", "达咩", "不同意", "不能"],
    "excited": ["开心", "好耶", "兴奋", "哈哈", "快乐", "庆祝", "耶", "棒", "喜事"],
    "praise": ["优秀", "真棒", "厉害", "赞", "奖励", "表扬", "棒", "夸奖", "棒棒"],
    "thanks": ["谢谢", "感谢", "多亏了", "比心", "鞠躬", "谢啦", "心心"],
    "goodnight": ["晚安", "好梦", "安安", "做个好梦", "梦里"],
    "moyu": ["摸鱼", "偷懒", "咸鱼", "放假", "下班", "溜了", "休息", "打工", "放空"],
    "ok": ["好的", "没问题", "ok", "好啊", "行", "明白"],
    "received": ["收到", "确认", "明白", "指令", "任务", "复制", "收到指令"],
    "sleep": ["睡觉", "困了", "睡了", "闭眼", "休息", "好困", "睡醒", "打哈欠"],
    "nomoney": ["没钱", "穷", "钱包", "预算", "贫穷", "买不起", "充值", "攒钱"],
    "sigh": ["唉", "难过", "可惜", "叹气", "累了", "桑心", "伤心", "委屈", "呜呜", "难受"],
    "blush": ["害羞", "脸红", "羞涩", "夸奖", "不好意思"],
    "love": ["喜欢", "贴贴", "抱抱", "爱你", "心心", "宠溺", "心动", "陪伴", "暖心"],
    "pleading": ["求求", "拜托", "可以吗", "求你", "可怜", "求求了"],
    "shy": ["不好意思", "羞涩", "害羞", "脸红"],
    "panic": ["慌乱", "完蛋", "天啊", "怎么会", "出事", "糟糕", "天啦", "啊？", "啊这"],
    "wow": ["哇", "天啊", "哇塞", "震惊", "吃惊", "天啦", "好厉害", "真的吗"],
    "confused": ["不懂", "困惑", "什么情况", "疑惑", "怎么回事", "懵", "不解"],
    "curious": ["好奇", "想知道", "悄悄", "八卦", "什么呢", "悄悄看"],
    "idea": ["主意", "办法", "解决", "得意", "分析", "看我的", "推测", "计划", "琢磨"],
    "busy": ["忙", "工作", "代码", "文件", "任务", "开发", "编写", "搬砖", "加班"],
    "sam": ["机甲", "萨姆", "装甲", "变身", "铠甲", "机甲"]
}


class MemeSelector:
    """表情包选择器 — 前缀索引 + 语义优先匹配 + CD 冷却。

    Usage:
        selector = MemeSelector()
        path = selector.pick("thinking", text="这到底是怎么回事？")
    """

    def __init__(self):
        # emotion → [Path, ...]  前缀索引
        self._index: dict[str, list[Path]] = {}
        # 上一张已选文件路径（避免连续两轮完全相同）
        self._last_path: Optional[str] = None
        self._rescan()

    def _rescan(self) -> None:
        """扫描内置+用户目录，按 {emotion}_ 前缀建索引。"""
        self._index = {e: [] for e in VALID_EMOTIONS}
        for directory in (BUILTIN_MEMES_DIR, USER_MEMES_DIR):
            if not directory.exists():
                continue
            for entry in directory.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in _VALID_EXTENSIONS:
                    continue
                name = entry.stem  # e.g. "happy_excited"
                for emo in VALID_EMOTIONS:
                    if name.startswith(emo + "_") or name == emo:
                        self._index[emo].append(entry)
                        break

    def reload(self) -> None:
        """用户丢新图后热刷新（无需重启）。"""
        self._rescan()

    def pick(self, emotion: str, text: str = "") -> Optional[str]:
        """根据 emotion 标签及文本语义匹配选择表情包。

        策略：
        - 优先尝试从同 emotion 候选中找出文件名描述与当前文本中关键词匹配的项（语义匹配）
        - 若有语义匹配成功的候选，则从匹配到的候选集中随机选择，优先忽略 CD 以确保精准表达
        - 若无语义匹配成功的候选，则退化为在同 emotion 候选中随机选
        - 若只有 1 个候选且和上次相同 → 跳过（避免完全重复）
        - 若 ≥2 个候选 → 随机选（自然变化）
        - 无候选 → 返回 None

        Args:
            emotion: 如 "happy" / "thinking" / "work"
            text: 当前对话的文本上下文（含用户输入和助手回复），用于语义辅助匹配

        Returns:
            文件绝对路径字符串，或 None。
        """
        if emotion not in self._index:
            return None

        candidates = self._index[emotion]
        if not candidates:
            return None

        # 1. 尝试基于语义关键词筛选候选表情包
        matched_candidates = []
        if text:
            text_lower = text.lower()
            for cand in candidates:
                name = cand.stem
                parts = name.split("_", 1)
                if len(parts) > 1:
                    desc = parts[1].lower()
                    # 检查文件名描述是否被显式提及，或其映射的关键词是否出现在文本中
                    keywords = _SEMANTIC_KEYWORDS.get(desc, [])
                    if desc in text_lower or any(kw in text_lower for kw in keywords):
                        matched_candidates.append(cand)

        # 2. 如果存在语义匹配项，优先从匹配项中进行挑选
        if matched_candidates:
            if len(matched_candidates) == 1:
                chosen = matched_candidates[0]
            else:
                # 尽量避免和上一张完全相同
                available = [p for p in matched_candidates if str(p) != self._last_path]
                if not available:
                    available = matched_candidates
                chosen = random.choice(available)
            self._last_path = str(chosen)
            return str(chosen)

        # 3. 兜底逻辑：无语义匹配，退化为原有的前缀随机及 CD 冷却规则
        if len(candidates) == 1 and str(candidates[0]) == self._last_path:
            return None

        if len(candidates) > 1:
            available = [p for p in candidates if str(p) != self._last_path]
            if not available:
                available = candidates
            chosen = random.choice(available)
        else:
            chosen = candidates[0]

        self._last_path = str(chosen)
        return str(chosen)

    def pick_always(self, emotion: str, text: str = "") -> Optional[str]:
        """无条件选择（忽略重复检查），用于模式切换等特殊场景。"""
        if emotion not in self._index:
            return None
        candidates = self._index[emotion]
        if not candidates:
            return None
        chosen = random.choice(candidates)
        self._last_path = str(chosen)
        return str(chosen)

    def list_all(self) -> dict[str, list[str]]:
        """调试用：列出所有已索引的表情包。"""
        return {e: [str(p) for p in paths] for e, paths in self._index.items() if paths}


# 模块级单例
_meme_selector: Optional[MemeSelector] = None


def get_meme_selector() -> MemeSelector:
    global _meme_selector
    if _meme_selector is None:
        _meme_selector = MemeSelector()
    return _meme_selector
