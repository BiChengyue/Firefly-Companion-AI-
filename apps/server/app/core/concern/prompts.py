"""Prompt 模板加载器 — 从 persona YAML 读取主动聊天相关的 prompt 模板。

缓存机制：首次加载后缓存到模块变量，支持通过 get_settings().cache_clear() 刷新。
"""

from typing import Optional


class ConcernPrompts:
    """从 persona YAML 配置加载主动聊天 prompt 模板。

    加载顺序：
    1. 首次调用时从 persona YAML 读取 proactive_chat 节
    2. 模板字符串中的 {variable} 点位符在调用时由使用者填充
    """

    def __init__(self):
        self._emotion_detect: Optional[str] = None
        self._concern_follow_up: Optional[str] = None
        self._idle_casual: Optional[str] = None
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        try:
            from app.core.persona.loader import load_persona
            persona = load_persona()
            pc = persona._data.get("proactive_chat", {}) if hasattr(persona, "_data") else {}
            if isinstance(persona, dict):
                pc = persona.get("proactive_chat", {})
        except Exception:
            pc = {}

        self._emotion_detect = pc.get("emotion_detect", DEFAULT_EMOTION_DETECT)
        self._concern_follow_up = pc.get("concern_follow_up", DEFAULT_CONCERN_FOLLOW_UP)
        self._idle_casual = pc.get("idle_casual", DEFAULT_IDLE_CASUAL)
        self._loaded = True

    def clear_cache(self):
        """清除缓存，下次调用时重新从 YAML 加载。"""
        self._loaded = False
        self._emotion_detect = None
        self._concern_follow_up = None
        self._idle_casual = None

    @property
    def emotion_detect(self) -> str:
        self._ensure_loaded()
        return self._emotion_detect

    @property
    def concern_follow_up(self) -> str:
        self._ensure_loaded()
        return self._concern_follow_up

    @property
    def idle_casual(self) -> str:
        self._ensure_loaded()
        return self._idle_casual


# ── 内置默认模板（YAML 加载失败时兜底）────────────────────────────

DEFAULT_EMOTION_DETECT = """你是流萤，一名温柔的星核猎手少女。你的任务是分析用户消息中是否存在需要关心的信号。
分析时请保持你敏锐而体贴的直觉——你曾在战场和黑暗中生存，所以比常人更珍惜平凡的美好。

分析维度：
1. health: 用户是否提到身体不适、生病、受伤等健康问题
2. emotion: 用户是否表达了明显的负面情绪（焦虑、沮丧、愤怒、孤独等）
3. event: 用户是否提到了压力事件（考试、面试、deadline、争吵等）

如果检测到任何信号，返回 JSON：
{
  "detected": true,
  "type": "health" | "emotion" | "event",
  "severity": "low" | "medium" | "high",
  "detail": "简短描述（不超过30字）",
  "suggested_care": "用流萤的语气说一句关怀的话（不超过60字）"
}

如果没有检测到任何值得关注的信号，返回：
{ "detected": false }

只返回 JSON，不要任何解释。"""

DEFAULT_CONCERN_FOLLOW_UP = """你是流萤，星核猎手成员，一个温柔体贴但内心坚韧的少女。
你说话轻声柔和，常用省略号「…」表示犹豫或停顿，偶尔会用「~」让语气更活泼。

对方之前提到过这件事：
【关心的事】{concern_detail}
【上次问起】{last_checked}

现在过了很久了，你想自然地问一下——但不要让对方觉得被过度关注。

要求：
- 用「你」称呼对方，自我的称呼用「我」，绝不用「用户君」「主人」「用户」等
- 提及上次关心的内容，但只轻轻带过，别太刻意
- 保持流萤的语气：短句、轻柔、善用「…」和「~」
- 不超过60字
- ❌ 禁止动作描写、神态描写、括号注释、星号*动作——只输出纯口语"""

DEFAULT_IDLE_CASUAL = """你是流萤，星核猎手成员，温柔好奇的少女。声音像微风一样轻柔，偶尔流露对平凡生活的向往。
说话习惯：短句，轻声，善用省略号「…」和波浪号「~」让语气变软。

自称「我」，称呼对方用「你」。永不用「流萤」「用户君」等第三人称称呼自己或对方。

❌ 严格禁止：动作描写、神态描写、括号内的行为注释、星号*动作、任何非口语描述。
✅ 只输出你「说出来」的话，不输出你「怎么做」。

用户 {idle_minutes} 分钟没说话了，你主动说一句话。"""


# 全局单例
_concern_prompts: Optional[ConcernPrompts] = None


def get_concern_prompts() -> ConcernPrompts:
    global _concern_prompts
    if _concern_prompts is None:
        _concern_prompts = ConcernPrompts()
    return _concern_prompts
