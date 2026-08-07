"""WebSocket 对话接口 — 对应 spec 阶段1/3/4。"""
import asyncio
import base64
import json
import os as _os
import random
import re
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
# 当前活动的空闲引擎引用，供“保存设置后热更新配置”使用（避免需重连 WS 才生效）
_active_idle_engine = None
from app.core.agent.approval import resolve_approval
from app.core.llm.base import LLMMessage
from app.core.llm.registry import LLMProviderRegistry
from app.core.memory.manager import active_concern, memory_manager
from app.core.persona.builder import build_system_prompt, build_authors_note
from app.core.persona.loader import load_persona
from app.core.hsr_lore import involves_game_lore
from app.core.logging_config import get_logger
from app.core.tools.builtin.core_tools import web_search

logger = get_logger("api.chat")

# ── QQ 通道协议（A2：channel=="qq" 时注入 data/skills/firefly-qq-protocol/SKILL.md）──
# 仓库根 = chat.py 向上 5 层（api → app → server → apps → 根）
_ROOT_DIR = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))))
_QQ_PROTOCOL_FILE = _os.path.join(_ROOT_DIR, "data", "skills", "firefly-qq-protocol", "SKILL.md")
_qq_protocol_cache = {"mtime": 0.0, "text": ""}


def _qq_protocol_block() -> str:
    """QQ 通道协议（消息格式 + 档位上限）。文件热生效；>8KB 截断；失败静默降级。"""
    global _qq_protocol_cache
    try:
        mtime = _os.path.getmtime(_QQ_PROTOCOL_FILE)
        if mtime != _qq_protocol_cache["mtime"]:
            with open(_QQ_PROTOCOL_FILE, encoding="utf-8") as f:
                text = f.read().strip()
            if len(text) > 8192:
                text = text[:8192]
            _qq_protocol_cache = {"mtime": mtime, "text": text}
        if _qq_protocol_cache["text"]:
            return "\n\n" + _qq_protocol_cache["text"]
    except Exception as e:
        logger.warning("[chat] QQ 协议文件读取失败: %s", e)
    return ""


# 主动聊天兜底台词池（LLM 返回空时随机选一条）
_PROACTIVE_FALLBACKS = [
    # ── 日常关心 ──
    "嘿，偷偷看看你在做什么……",
    "忙了这么久，要不要起来活动一下？",
    "你还在呀，真好~",
    "喝点水休息一下吧，别太累了",
    "今天的天气真不错呢……",
    # ── 温柔问候 ──
    "有点想你了，过来打个招呼~",
    "虽然不知道说什么，但还是想陪着你",
    "听到键盘声了，在写什么好东西呀？",
    "夜深了呢，要不要早点休息…",
    "看到你还在线，我也安心了",
    # ── 轻松闲聊 ──
    "我刚刚发了会儿呆，你呢？",
    "你知道吗，萤火虫发光是为了吸引同伴哦",
    "总感觉今天会是美好的一天~",
    "如果能一起去看看星星就好了",
    "你喜欢什么音乐呀？我最近在听一首很温柔的歌",
]

router = APIRouter(tags=["chat"])

_PREFIX_TOKEN = "TOKEN:"
_PREFIX_THINKING = "THINKING:"
_PREFIX_ERROR = "ERROR:"

_EMOTION_KEYWORDS: list[tuple[str, list[str]]] = [
    ("happy",     ["开心", "高兴", "太棒", "喜欢", "哈哈", "好耶", "✨", "嘿嘿", "加油",
                   "兴奋", "感谢", "谢谢", "比心", "暖心", "温暖", "拥抱", "鼓励",
                   "当然可以", "乐意", "太好", "我会努力的"]),
    ("sad",       ["难过", "伤心", "可惜", "遗憾", "哭", "呜", "唉", "心疼",
                   "叹气", "疲惫", "辛苦", "没钱", "穷", "困难", "不行了",
                   "安慰", "抱抱", "陪伴"]),
    ("angry",     ["生气", "愤怒", "讨厌", "烦", "无语", "哼",
                   "不可以", "不行", "不能", "拒绝", "绝不", "警告", "别想", "坚决"]),
    ("shy",       ["害羞", "脸红", "不好意思", "扭捏", "羞涩",
                   "抱歉，我并非有意隐瞒", "脸红了"]),
    ("thinking",  ["思考", "想想", "让我想", "稍等", "分析",
                   "疑惑", "困惑", "不解", "为什么", "怎么", "好奇", "大概", "也许", "可能", "推测",
                   "估计", "应该", "觉得", "认为", "琢磨", "会呢", "看看", "听听",
                   "嗯…", "那个…"]),
    ("surprised", ["哇", "惊讶", "没想到", "天啊", "不会吧",
                   "真的吗", "居然", "咦", "哎", "哇塞", "哎？", "咦？"]),
]


def _cn_num_to_int(s: str) -> int | None:
    """中文数字转阿拉伯数字（支持 十/十二/二十/五/零 等常用表达），无法识别返回 None。"""
    t = s.strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    cn_map = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if t in cn_map:
        return cn_map[t]
    # 十 / 十几 / 几十 / 几十几
    m = re.match(r"^(十|([一二三四五六七八九])十)?([一二三四五六七八九])?$", t)
    if not m:
        return None
    tens = 0
    if m.group(1):
        if m.group(1) == "十":
            tens = 10
        elif m.group(2):
            tens = cn_map[m.group(2)] * 10
    ones = cn_map[m.group(3)] if m.group(3) else 0
    val = tens + ones
    return None if val == 0 and t != "零" else val


def parse_chinese_reminder_intent(text: str) -> dict | None:
    """从用户输入中智能捕获提醒意图与目标到期时间。
    例如: "流萤，提醒我明天早上八点告诉我该起床了"
    """
    if not any(k in text for k in ("提醒", "闹钟", "叫醒", "定时")):
        return None

    # 剔除尾部可能附带的聊天消息时间戳（如 21:23）
    clean_raw = re.sub(r"\b\d{1,2}:\d{2}\b", "", text).strip()

    now = datetime.now()
    target_dt = None
    clean_text = clean_raw

    # 1. 相对时间：秒/分/时（支持阿拉伯数字与中文数字，如"10秒""十秒""十二分钟"）
    duration_re = re.compile(r"([0-9]+|[一二三四五六七八九十百]+)\s*(秒后?|s|sec|分钟后?|分后|m|min|小时后?|时后|h|hour)", re.IGNORECASE)
    duration_m = duration_re.search(clean_raw)

    if duration_m:
        num_val = _cn_num_to_int(duration_m.group(1))
        unit = duration_m.group(2).lower()
        if num_val is not None:
            if unit in ("秒后", "秒", "s", "sec"):
                target_dt = now + timedelta(seconds=num_val)
            elif unit in ("分后", "分钟后", "分", "m", "min"):
                target_dt = now + timedelta(minutes=num_val)
            else:
                target_dt = now + timedelta(hours=num_val)
            clean_text = clean_text.replace(duration_m.group(0), "")

    # 2. 绝对时间：明天/今天/后天 + 时间点
    if not target_dt:
        days_add = 0
        if "明天" in clean_raw:
            days_add = 1
            clean_text = clean_text.replace("明天", "")
        elif "后天" in clean_raw:
            days_add = 2
            clean_text = clean_text.replace("后天", "")
        elif "今天" in clean_raw:
            days_add = 0
            clean_text = clean_text.replace("今天", "")

        cn_num_map = {"一":1, "二":2, "三":3, "四":4, "五":5, "六":6, "七":7, "八":8, "九":9, "十":10}

        # 优先匹配中文时间点: (早上|上午|中午|下午|晚上)? N点(半|M分)?
        m_cn = re.search(r"(早上|上午|中午|下午|晚上)?\s*(\d+|一|二|三|四|五|六|七|八|九|十)+点(半|\d+分)?", clean_raw)
        clock_m = re.search(r"(\d{1,2})[:：](\d{2})", clean_raw)

        if m_cn:
            period = m_cn.group(1) or ""
            num_str = m_cn.group(2)
            minute_str = m_cn.group(3) or ""

            h = int(num_str) if num_str.isdigit() else cn_num_map.get(num_str, 8)
            if period in ("下午", "晚上") and h < 12:
                h += 12
            elif period in ("早上", "上午") and h == 12:
                h = 0

            m = 30 if "半" in minute_str else (int(re.sub(r"\D", "", minute_str)) if re.sub(r"\D", "", minute_str) else 0)
            target_dt = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=days_add)
            clean_text = clean_text.replace(m_cn.group(0), "")
        elif clock_m:
            hour = int(clock_m.group(1))
            minute = int(clock_m.group(2))
            target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_add)
            clean_text = clean_text.replace(clock_m.group(0), "")

        if target_dt and target_dt <= now and days_add == 0:
            target_dt += timedelta(days=1)

    if not target_dt:
        return None

    # 清理提醒语名前缀
    clean_text = re.sub(r"^(流萤[，,]*)?\s*(请)?(帮我)?(提醒我|提醒|定闹钟|叫醒)\s*", "", clean_text).strip()
    clean_text = re.sub(r"^(告诉我|去|做)\s*", "", clean_text).strip()
    if not clean_text:
        clean_text = "定时提醒"

    timestamp_ms = int(target_dt.timestamp() * 1000)
    display_time = target_dt.strftime("%H:%M") if target_dt.day == now.day else target_dt.strftime("明天%H:%M")

    return {
        "text": f"{clean_text} ({display_time})",
        "dueTimestamp": timestamp_ms
    }


def _resolve_meme_url(meme_path: str | None) -> str | None:
    """将表情包本地物理磁盘路径转换为前端可引用的 Web 相对 URL。"""
    if not meme_path:
        return None
    p_str = str(meme_path).replace("\\", "/")
    if "/memes/" in p_str:
        return "/memes/" + p_str.split("/memes/")[-1]
    if "/photo/" in p_str:
        return "/photo/" + p_str.split("/photo/")[-1]
    return meme_path

_USER_EMOTION_HINTS: list[tuple[str, list[str]]] = [
    ("surprised", ["什么情况", "真的吗", "怎么会", "不是吧", "不会吧", "啊？"]),
    ("thinking",  ["猜", "为什么", "怎么", "好奇", "想想", "分析", "什么意思", "什么原因"]),
    ("shy",       ["害羞", "表白", "你真好看", "喜欢你"]),
    ("happy",     ["哈哈", "开心", "高兴", "不错", "太好了", "棒"]),
    ("sad",       ["累了", "好难", "太惨", "唉", "辛苦你了", "好累"]),
]


def _detect_emotion(text: str, user_text: str = "") -> str:
    if user_text:
        for emotion, keywords in _USER_EMOTION_HINTS:
            if any(kw in user_text for kw in keywords):
                return emotion
    for emotion, keywords in _EMOTION_KEYWORDS:
        if any(kw in text for kw in keywords):
            return emotion
    return "neutral"


# ═══════════════════════════════════════════════════
#  意图分类（规则预筛 + LLM 兜底）
# ═══════════════════════════════════════════════════

_TASK_VERBS = {
    # 创建/写入
    "创建", "新建", "写", "生成", "添加", "制作",
    # 删除
    "删除", "删掉", "移除", "去掉",
    # 修改
    "修改", "替换", "改", "更新", "修正",
    # 搜索/查询
    "搜索", "查找", "搜", "找", "查", "查询", "查看", "看下", "看一下",
    # 了解/分析
    "了解", "分析", "解释", "说明", "总结", "概括", "概述", "翻译",
    # 获取
    "获取", "抓取", "下载", "拉取", "读取", "解析", "提取",
    # 文件操作
    "复制", "移动", "打开", "关闭", "保存", "导出", "导入",
    # 执行
    "运行", "执行", "启动", "停止", "重启", "设置", "调整", "优化",
    # 安装/构建
    "安装", "卸载", "配置", "编译", "构建", "部署", "发布",
    # 检查
    "检查", "检测", "测试", "调试", "修复", "解决",
    # 展示
    "显示", "展示", "列出", "统计", "计算", "比较",
    # 英文
    "create", "write", "delete", "remove", "run",
    "install", "build", "compile", "deploy",
    "search", "find", "fetch", "read", "parse",
    "check", "test", "fix", "debug",
}

_FS_WORDS = {
    "文件", "目录", "文件夹", "项目", "代码", "文档", "md", "py", "js",
    "ts", "vue", "json", "yaml", "配置", "日志", "log", "报错", "错误",
    "error", "bug", "测试", "test", "工具", "命令",
}

_DIRECTION_WORDS = {
    "帮我", "请帮我", "能不能帮", "可以帮我", "给我", "替我",
    "帮我做", "帮我写", "帮我查", "帮我看", "帮我找", "帮我创建",
    "帮我改", "帮我删",
}

# 过去时态标记：含有这些词的问题通常是回忆/回顾类闲聊，不应进入 Agent
_PAST_MARKERS = {
    "之前", "上次", "昨天", "上周", "上次你", "刚才你", "你刚才", "你之前",
    "今天", "昨晚", "那天", "前几天", "前些天", "刚才", "刚刚",
    "记得", "回忆", "记不记得", "还记得",
}

# 闲聊模式（命中任一即判 chat，跳过 Agent）
_CHAT_PATTERNS = [
    # 问候/告别
    re.compile(r"^(你好|嗨|哈[喽啰]|早上好|下午好|晚上好|晚安|再见|拜拜)"),
    # 感谢
    re.compile(r"^(谢谢|感谢|多谢|不客气)"),
    # 语气词
    re.compile(r"^(嗯|哦|啊|咦|唉|哈哈|嘿嘿|呵呵)\s*$"),
    # 情绪表达
    re.compile(r"(开心|难过|伤心|高兴|好烦|无聊|好累|好困|烦死|太棒|真棒|太差|真好)"),
    re.compile(r"(喜欢|讨厌|想哭|郁闷|焦虑|担心|害怕|生气|愤怒)"),
    # 用户自己回忆
    re.compile(r"(想起来|忽然想起|突然想到|我刚刚|我忽然|我记得|我想到|梦到|昨晚梦)"),
    # 询问 AI 是否记得 / 回忆过去
    re.compile(r"(你还记得|你记得吗|你记不记得|你还记不记得|你还记得.*吗)"),
    re.compile(r"(帮我回忆|帮我回想|帮我回顾|帮我想想|帮我回顾一下)"),
    # 询问 AI 意见/感觉
    re.compile(r"^(你觉得|你认为|你感觉|你觉得呢|你怎么想|你怎么看)"),
    # 询问 AI 知不知道
    re.compile(r"(你知不知道|你知道.*吗|你知道.*不|你能知道)"),
    # 纯粹好奇/闲聊问题（带吗/？的简短问句且无任务动词）
    re.compile(r"^(你|他|她|我.*(吗|呢)[\?？]?\s*$)"),
    # 确认/收到
    re.compile(r"^(好的|知道了|明白了|懂了|OK|ok|收到)\s*$"),
    # 自我介绍
    re.compile(r"^(我是|我叫|我也|我还|我在)"),
    # 解释/原因类问题（非技术）
    re.compile(r"^(为什么你|你怎么|你干嘛|你咋)"),
]


def _has_task_verb(text: str) -> bool:
    """检查文本中是否包含任务型动词（用于区分 task vs chat）。"""
    return any(v in text.lower() for v in _TASK_VERBS)


def _is_casual_chat(text: str) -> bool:
    """规则预筛第一层：明显为闲聊，跳过 Agent。
    
    增强逻辑：命中聊天模式后，额外检查是否同时包含任务动词，
    防止"你记得我昨天说了什么吗？帮我写一篇文章"这类复合查询误判。
    """
    text_lower = text.lower()
    if any(w in text_lower for w in _FS_WORDS):
        return False
    if not any(p.search(text) for p in _CHAT_PATTERNS):
        return False
    # 防穿刺：命中聊天模式但同时包含任务动词 → 交给 LLM 分类器判断
    if _has_task_verb(text):
        return False
    return True


def _is_clear_task(text: str) -> bool:
    """规则预筛第二层：明显为任务请求，直接走 Agent。
    必须同时满足：有任务动词 + 有方向词 + 非过去时态。"""
    text_lower = text.lower()
    if not any(v in text_lower for v in _TASK_VERBS):
        return False
    if not any(v in text_lower for v in _DIRECTION_WORDS):
        return False
    if any(v in text_lower for v in _PAST_MARKERS):
        return False
    return True


# 搜索/新闻类查询关键词（用于快速通道，绕过 Agent planner）
_SEARCH_KEYWORDS = ("搜索", "搜一下", "搜一搜", "查一下", "查查", "帮我查",
                    "上网搜", "今天新闻", "最新新闻", "今日新闻", "发生了什么事",
                    "新闻", "资讯", "热点", "头条", "今天发生", "最新发生")


def _is_search_query(text: str) -> bool:
    """判断是否为搜索/新闻类简单信息查询（不走 Agent，走快速通道）。"""
    t = text.lower()
    return any(k in t for k in _SEARCH_KEYWORDS)


async def _classify_intent(provider, text: str) -> str:
    """LLM 兜底分类器 — 判断用户消息应走 Agent（task）还是直接聊天（chat）。

    上下文：聊天模式下，用户的所有记忆/对话历史已预先注入到系统 prompt 中，
    因此「回忆过去」「询问你是否记得某件事」这类问题直接用 LLM 知识回答即可，
    无需调用任何工具，应判为 chat。

    只有需要执行实际操作的请求才判 task：文件操作、代码修改、web 搜索、数据查询等。
    """
    _CLASSIFY_SYSTEM = (
        "你是一个意图分类器。判断用户消息是聊天(chat)还是任务(task)。\n\n"
        "chat（聊天）：回忆过去、问你的感受、闲聊、倾诉、问候、询问你是否知道某事、\n"
        "  回忆对话历史（你不需要搜索，历史已在你的上下文中）。\n"
        "  chat 示例：你还记得我昨天说了什么吗 / 今天心情怎么样 / 你知道流萤是谁吗 / \n"
        "  我之前跟你说的那个事你还记得吗 / 你觉得这个方案怎么样\n\n"
        "task（任务）：需要你执行具体操作的任务。\n"
        "  task 示例：帮我搜索一下Python最新版本 / 创建hello.txt文件 / \n"
        "  分析这个项目的代码结构 / 查看项目中的md文件\n\n"
        "规则：只要不需要调用工具就能回答 → chat；需要调用工具才能回答 → task。\n"
        "只回复 chat 或 task。"
    )
    try:
        response = await provider.chat(
            [
                LLMMessage(role="system", content=_CLASSIFY_SYSTEM),
                LLMMessage(role="user", content=text),
            ],
            temperature=0,
            max_tokens=8,
        )
        result = response.content.strip().lower()
        logger.info("[路由] LLM 分类器结果: %r → %s", text[:40], result)
        return "chat" if "chat" in result else "task"
    except Exception:
        logger.warning("[路由] LLM 分类器失败，保守判 task")
        return "task"  # 失败时保守走 Agent（有 planner fallback 兜底）


async def _send_json(ws: WebSocket, data: dict) -> None:
    await ws.send_text(json.dumps(data, ensure_ascii=False))


async def _send_mode_config(ws: WebSocket, mode: str) -> None:
    persona = load_persona()
    mode_config = persona.get_mode_config(mode)
    await _send_json(ws, {
        "type": "mode_switched", "mode": mode,
        "theme": mode_config.get("theme", {}),
        "hudVisible": mode_config.get("hud_visible", False),
        "thinkVisible": mode_config.get("think_visible", False),
        "proactiveCare": mode_config.get("proactive_care", False),
    })


@router.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    await ws.accept()
    settings = get_settings()
    persona = load_persona()
    mode = settings.mode.current
    history: list[LLMMessage] = []
    max_history = settings.memory.short_term_window
    current_session_id: str | None = None
    concern_fired_this_connection = False
    voice_enabled = True  # 语音默认开启，可通过 voice_toggle 消息控制
    daily_unlocked = False  # 日常模式解除限制标志

    # 记录原始工作目录（空间取消选中时回退到此）
    _original_cwd = _os.getcwd()

    # ── 引擎 B：空闲主动聊天引擎 ─────────────────────────
    from app.core.concern.idle_engine import IdleChatEngine
    pc_config = settings.proactive_chat if hasattr(settings, "proactive_chat") else None
    idle_engine: IdleChatEngine | None = None
    engine_b_enabled = (
        mode == "daily" and pc_config and pc_config.enabled
    )
    if engine_b_enabled:
        idle_engine = IdleChatEngine(
            idle_seconds=pc_config.idle_minutes * 60,
            quiet_hours_start=pc_config.quiet_hours_start,
            quiet_hours_end=pc_config.quiet_hours_end,
            daily_limit=pc_config.daily_limit,
            mode=mode,
        )

        async def _on_idle_trigger() -> str | None:
            """空闲触发回调：生成主动聊天内容。"""
            live_settings = get_settings()
            p = LLMProviderRegistry.create(
                live_settings.llm.provider, api_key=live_settings.llm.api_key,
                base_url=live_settings.llm.base_url, model=live_settings.llm.model,
                temperature=0.85, max_tokens=256, enable_thinking=False,
            )
            recalled = await memory_manager.recall("", mode, top_k=3) if settings.memory.long_term_enabled else []
            content = await active_concern.generate_proactive_content(
                p, mode=mode, idle_minutes=live_settings.proactive_chat.idle_minutes,
                recalled_memories=recalled,
            )
            if not content:
                content = random.choice(_PROACTIVE_FALLBACKS)
            content = _strip_action_desc(content)
            await _send_json(ws, {
                "type": "proactive_speech",
                "content": content,
                "source": "idle_chat",
                "motion": "greet",
                "expression": "smile",
            })
            # 持久化：加入 LLM 上下文 + 写入聊天记录（重启后仍可见）
            history.append(LLMMessage(role="assistant", content=content))
            memory_manager.add_message("assistant", content)
            if current_session_id:
                try:
                    memory_manager.save_chat_message(
                        current_session_id, "assistant", content, mode, "neutral")
                except Exception as e2:
                    logger.error("保存主动聊天消息失败: %s", e2)
            # 主动聊天不触发 TTS（避免反复拉起 GPT-SoVITS 子进程）
            return content
            return content

        idle_engine.set_callback(_on_idle_trigger)
        idle_engine_future = asyncio.create_task(idle_engine.start())
        # 登记为当前活动引擎，供“保存设置后热更新”使用
        global _active_idle_engine
        _active_idle_engine = idle_engine

    def _strip_emoji(text: str) -> str:
        """移除文本中的 emoji，避免 TTS 引擎读出乱码。"""
        return re.sub(
            r'[\U0001F600-\U0001F64F'
            r'\U0001F300-\U0001F5FF'
            r'\U0001F680-\U0001F6FF'
            r'\U0001F1E0-\U0001F1FF'
            r'\U00002702-\U000027B0'
            r'\U0001F900-\U0001F9FF'
            r'\U0001FA00-\U0001FA6F'
            r'\U0001FA70-\U0001FAFF'
            r'\u2600-\u27BF'
            r'\uFE0F\u200D'
            r']+', '', text)

    # 动作 / 神态 / 舞台指示常见词，用于识别括号内是否为角色扮演动作描写。
    # 命中时才删除该括号，避免误删正常的解释性括号（如"（详见附录）"）。
    _ACTION_WORDS = (
        '点头', '摇头', '微笑', '笑', '叹气', '轻触', '伸手', '眨眼', '微微', '轻轻',
        '眼神', '望', '看向', '转头', '低头', '抬头', '侧身', '靠近', '握', '靠',
        '抱', '垂', '仰', '俯', '歪', '挑眉', '抿', '沉默', '停顿', '呼吸', '歪头',
        '挥手', '招手', '指', '拍', '摸', '抚', '盯', '浅笑', '莞尔', '神色', '缓缓',
        '身子', '双手', '目光', '语气', '轻声', '低声', '环顾', '转身', '起身', '坐下',
        '站起', '凑近', '俯身', '仰头', '身体', '嘴角', '露', '眯', '伸', '侧', '探',
        '倾', '拢', '攥', '扶', '托', '摆', '晃', '顿', '扬', '弯', '招', '比', '凑',
        '抿嘴', '浅笑', '倾身', '歪头', '垂眸', '抬眼', '闭眼', '叹息', '凝望', '注视',
    )
    _ACTION_RE = re.compile(r'[（(]\s*[^）)\n]{0,30}\s*[）)]')

    def _strip_action_desc(text: str) -> str:
        """剥离模型偶发输出的舞台指示 / 动作描写（仅角色扮演语境）。

        persona 已明确禁止圆括号()、全角括号（）包裹的动作/旁白，但模型仍可能
        绕过（尤其用全角括号），故在服务端做兜底。

        注意：本函数刻意**只处理圆括号**，且**仅当括号内含动作/神态词时才删除**：
        - 不动方括号 [] 与星号 * —— 它们常用于代码、列表、引用、加粗等正常任务
          产出，无差别剥离会丢失用户/工具的真实内容；
        - 保留不含动作词的圆括号（如"（详见附录）""（2026 年发布）"），避免误伤
          正常解释性文字。
        因此即便作用在 Agent 任务结果上也不会破坏代码 / 列表 / 链接等内容。
        """
        if not text:
            return text

        def _repl(m: 're.Match') -> str:
            inner = m.group(0)
            core = inner[1:-1].strip()
            # 仅当括号内容含动作 / 神态词时才视为舞台指示并删除
            if any(w in core for w in _ACTION_WORDS):
                return ''
            return inner

        # 预编译正则：仅匹配半角/全角圆括号包裹的短内容
        cleaned = _ACTION_RE.sub(_repl, text)
        # 合并因删除产生的多余空格 / 空行
        cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
        cleaned = re.sub(r'\n{2,}', '\n', cleaned)
        return cleaned.strip()

    async def _send_tts(text: str):
        """预连接优化：先推送音频 URL → 浏览器提前连接 → 再生成 TTS → 文件就绪即播。

        2026-08-07 分条语音：完整回复拆成段落逐条合成（与文字分条对应），
        每段独立语音——短文本合成快、停顿自然、不切段拼接缺字。
        """
        try:
            from app.core.voice.tts import get_tts_service
            import hashlib
            clean = _strip_emoji(text).strip()
            # 剔除动作/神态段（*星号* 包裹）——动作不朗读，只读语言（2026-08-07）
            clean = re.sub(r'\*[^*]*\*', '', clean)
            # 跳过省略号：省略号在 Edge-TTS 中产生犹豫停顿，跳过可避免不自然的顿挫语调
            clean = re.sub(r'…+', '', clean)
            if not clean:
                return
            svc = get_tts_service()

            live_settings = get_settings()
            provider = (live_settings.voice.tts.engine or "edge-tts").lower()
            voice_id = live_settings.voice.tts.voice or "zh-CN-XiaoyiNeural"

            # 分条：与输出总线 split_reply_chunks 同规则（换行拆 + 最多 4 段，超长合并末段）
            segs = [s.strip() for s in re.split(r"\n+", clean) if s.strip()]
            if len(segs) > 4:
                segs = segs[:3] + ["".join(segs[3:])]
            if len(segs) <= 1:
                segs = [clean]

            # ① 先一次性推送所有段 URL——桌宠立即预加载全部（文件就绪即播），
            #    生成流水线化：听段 1 时段 2 已在生成，段间几乎无缝（2026-08-07）
            urls: list[str] = []
            for seg in segs:
                cache_key = hashlib.md5(f"{provider}_{voice_id}_{seg}".encode("utf-8")).hexdigest()
                audio_url = f"http://127.0.0.1:8765/api/voice/file/{cache_key}.wav"
                urls.append(audio_url)
                await _send_json(ws, {
                    "type": "voice_audio",
                    "audioUrl": audio_url,
                    "text": seg,
                })

            # ② 逐段生成（引擎单实例串行，物理限制；文件依次就绪，桌宠播完一段下一段已好）
            for seg in segs:
                await svc.generate_speech(seg, provider=provider, voice_id=voice_id)
                logger.info("推送 TTS (%s/%s): %s...", provider, voice_id, seg[:20])
        except Exception as e:
            logger.error("TTS 生成失败: %s: %s", type(e).__name__, e)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            try:
                msg_type = msg.get("type", "")

                # ─── 终止生成 ───────────────────────────────────────
                # cancel 仅在生成进行中有意义（由 _check_ws_queue 在生成前/中处理）
                # 若在无活跃生成时收到 cancel，静默忽略
                if msg_type == "cancel":
                    continue

                # ─── 日常模式解除限制 ─────────────────────────────────
                if msg_type == "daily_unlock":
                    daily_unlocked = msg.get("unlocked", False)
                    await _send_json(ws, {"type": "daily_unlocked", "unlocked": daily_unlocked})
                    continue

                # ─── 模式切换 ─────────────────────────────────────────
                if msg_type == "mode_switch":
                    new_mode = msg.get("mode", mode)
                    if new_mode in ("daily", "work"):
                        mode = new_mode
                        if current_session_id:
                            from app.core import db as _db
                            history.clear()
                            memory_manager.clear_short_term()
                            history_msgs = _db.load_history(current_session_id, limit=max_history, mode=mode)
                            for hm in history_msgs:
                                history.append(LLMMessage(role=hm["role"], content=hm["content"]))
                                memory_manager.add_message(hm["role"], hm["content"], counting_for_extract=False)
                        await _send_mode_config(ws, mode)
                    continue

                # ─── 普通对话 ───────────────────────────────────────
                if msg_type == "chat":
                    content = msg.get("content", "").strip()
                    channel = msg.get("channel", "")
                    if not content:
                        continue

                    # 重置空闲计时器（引擎 B）
                    if idle_engine:
                        idle_engine.reset()

                    # 会话持久化
                    sid = msg.get("sessionId")
                    if sid and sid != current_session_id:
                        current_session_id = sid
                        from app.core import db as _db
                        existing = _db.get_session(sid)
                        if existing is None:
                            _db.create_session(sid, "新会话", mode)
                        history.clear()
                        memory_manager.clear_short_term()
                        history_msgs = _db.load_history(sid, limit=max_history, mode=mode)
                        for hm in history_msgs:
                            history.append(LLMMessage(role=hm["role"], content=hm["content"]))
                            memory_manager.add_message(hm["role"], hm["content"], counting_for_extract=False)

                    # 0ms 乐观响应 ACK（前端收到立即解除全屏 Loading，展示打字思考指示）
                    await _send_json(ws, {"type": "ack_received"})

                    # 构建系统 Prompt 与记忆召回（1.5s 熔断强保护 + 并行异步化）
                    sam_sub_tone = "execution" if mode == "work" else None
                    system_prompt = build_system_prompt(
                        persona, mode=mode, sam_sub_tone=sam_sub_tone,
                        user_message=content, model_name=get_settings().llm.model,
                        daily_unlocked=daily_unlocked,
                    )

                    if settings.memory.long_term_enabled:
                        try:
                            # 1.5 秒强熔断：并发执行记忆召回，超时自动融断以保证秒回体验
                            recalled = await asyncio.wait_for(
                                memory_manager.recall(content, mode, top_k=5),
                                timeout=1.5
                            )
                            if recalled:
                                mem_lines = memory_manager._format_memories_for_prompt(recalled)
                                if mem_lines:
                                    system_prompt += mem_lines
                        except asyncio.TimeoutError:
                            logger.warning("[chat] 记忆召回超时 (>1.5s)，熔断跳过以保障响应速度")
                        except Exception as e_rec:
                            logger.warning("[chat] 记忆召回异常: %s", e_rec)

                    # 日常模式：只读工具可用，文件/代码操作需切换到工作模式（除非已解锁）
                    if mode == "daily" and not daily_unlocked:
                        system_prompt += (
                            "\n\n## 重要提示：日常模式限制\n"
                            "你当前处于**日常模式**。\n"
                            "✅ 你可以使用：查阅网页（web_search/web_fetch）、获取当前时间（get_datetime）。\n"
                            "❌ 你不能使用：创建/修改/删除文件、执行shell命令等操作。\n"
                            "如果用户要求你写文档、创建文件、执行命令等操作，请以流萤的少女口吻温柔提醒他切换到工作模式。"
                            "例如：「唔…这种操作需要萨姆来帮你呢。要不要切换到工作模式看看？」\n"
                            "**注意**：仅在用户明确要求文件操作时才提示，纯聊天无需提及。\n"
                            "**关于查阅信息**：当你需要查阅游戏资讯、最新公告或不确定的内容时，请主动使用 web_search 或 web_fetch 查阅——但回答时保持角色口吻，绝不提及「搜索」「联网」「查阅」等字眼。"
                        )

                    history.append(LLMMessage(role="user", content=content))
                    memory_manager.add_message("user", content)
                    # 强记忆实时记录：绕过批提取周期，直接写入记忆（偏好/人际关系/行程事件）
                    _try_record_instant_memories(content, memory_manager, ws)
                    trimmed = history[-max_history:]
                    an_text = build_authors_note(
                        persona, mode=mode, daily_unlocked=daily_unlocked, sam_sub_tone=sam_sub_tone
                    )
                    # QQ 通道协议（CONTRACTS §4「按端风格生成」：生成侧收到 channel，
                    # 注入 QQ 严格消息格式 + 档位上限，追加在 author's note 区为最高优先级）
                    if channel == "qq":
                        an_text = an_text + _qq_protocol_block()
                    an_msg = LLMMessage(role="system", content=an_text)
                    messages = [LLMMessage(role="system", content=system_prompt), *trimmed, an_msg]

                    # 持久化用户消息
                    if current_session_id:
                        try:
                            memory_manager.save_chat_message(current_session_id, "user", content, mode, None)
                        except Exception as e2:
                            logger.error("保存用户消息失败: %s", e2)

                    # 智能捕获用户对话中的提醒意图
                    parsed_rem = parse_chinese_reminder_intent(content)
                    if parsed_rem:
                        try:
                            memory_manager.save_memory("promise", parsed_rem["text"], mode=mode, confidence=0.95)
                            rem_id = f"r_{int(time.time()*1000)}"
                            await _send_json(ws, {
                                "type": "reminder_created",
                                "reminder": {
                                    "id": rem_id,
                                    "text": parsed_rem["text"],
                                    "dueTimestamp": parsed_rem["dueTimestamp"],
                                    "fromApi": True
                                }
                            })
                        except Exception as e_rem:
                            logger.warning("自动创建提醒失败: %s", e_rem)

                    # 动态读取最新配置（确保前端保存 API Key 后实时生效）
                    live_settings = get_settings()

                    # 创建 Provider
                    provider = LLMProviderRegistry.create(
                        live_settings.llm.provider, api_key=live_settings.llm.api_key,
                        base_url=live_settings.llm.base_url, model=live_settings.llm.model,
                        temperature=live_settings.llm.temperature, max_tokens=live_settings.llm.max_tokens,
                        enable_thinking=live_settings.llm.enable_thinking,
                    )

                    # ── 引擎 A：主动关怀（情绪检测 + 关怀队列） ─────────
                    should_check_concern = (
                        mode == "daily" and
                        (
                            persona.get_mode_config("daily").get("proactive_care", False) or
                            daily_unlocked
                        )
                    )
                    if should_check_concern:
                        # 引擎 A-1：情绪检测
                        try:
                            care_result = await active_concern.detect_and_queue(
                                provider, content, mode=mode,
                            )
                            if care_result.get("care_text"):
                                await _send_json(ws, {
                                    "type": "concern",
                                    "content": care_result["care_text"],
                                    "id": care_result.get("concern_id", ""),
                                })
                                logger.info("[关怀] 引擎A触发: %s", care_result["care_text"][:50])
                        except Exception as e:
                            logger.debug("[关怀] 情绪检测跳过: %s", e)

                        # 引擎 A-2：检查 pending 关怀队列并复查（非本次触发的）
                        try:
                            pending = await active_concern.check_pending(mode=mode)
                            for concern_item in pending[:1]:  # 每次最多复查一条
                                follow_up = await active_concern._generate_follow_up(
                                    provider, concern_item
                                )
                                if follow_up:
                                    await _send_json(ws, {
                                        "type": "proactive_speech",
                                        "content": follow_up,
                                        "source": "concern_follow_up",
                                        "motion": "gentle",
                                        "expression": "caring",
                                    })
                                    logger.info("[关怀] 复查跟进: %s", follow_up[:50])
                        except Exception as e:
                            logger.debug("[关怀] 复查跳过: %s", e)

                        # 过期清理
                        try:
                            expired_count = active_concern.expire_stale(mode=mode)
                            if expired_count > 0:
                                logger.info("[关怀] 自动过期 %d 条关怀项", expired_count)
                        except Exception:
                            pass

                        # 引擎 A-3：检查用户正面回复 → 解析 pending 关怀
                        _POSITIVE_RESOLVE_PATTERNS = [
                            "好多了", "好多了谢谢", "谢谢关心", "没事了",
                            "好了", "不用担心", "没事啦", "已经好了",
                            "恢复", "痊愈", "不疼了",
                        ]
                        if any(p in content for p in _POSITIVE_RESOLVE_PATTERNS):
                            try:
                                pending = await active_concern.check_pending(mode=mode)
                                for concern_item in pending:
                                    active_concern.resolve_concern(concern_item["id"])
                                    logger.info("[关怀] 用户正面回复，解析关怀项: %s", concern_item["id"])
                            except Exception:
                                pass
                    elif mode == "daily" and not concern_fired_this_connection:
                        # 向后兼容：日常模式第一句问候（proactive_care=false 但仍是 daily）
                        try:
                            should_care = active_concern.should_fire("first_chat", "daily")
                        except Exception:
                            should_care = False
                        if should_care:
                            concern_fired_this_connection = True
                            persona_cfg = persona.get_mode_config("daily")
                            care_text = persona_cfg.get("greeting", "今天有什么我能帮忙的吗？")
                            await _send_json(ws, {"type": "concern", "content": care_text})
                            try:
                                active_concern.record("first_chat", care_text, "daily")
                            except Exception:
                                pass

                    # 路由：工作模式 / 日常模式（已解锁）→ Agent 任务循环
                    use_agent = False
                    lore_only = False
                    if mode == "work":
                        # 萨姆模式：全量走 Agent，由 planner 决定是否需要工具
                        use_agent = True
                    elif mode == "daily" and daily_unlocked:
                        # 日常模式已解锁：意图分类路由
                        # 游戏设定/角色事实类问题 → 开启 Agent。
                        if involves_game_lore(content) and not any(
                            k in content for k in ("公告", "新闻", "资讯", "直播", "活动")
                        ):
                            use_agent = True
                            lore_only = False
                        # 含 URL → 强制走 Agent（需要 web_fetch 抓取）
                        elif "http://" in content or "https://" in content:
                            use_agent = True
                        elif _is_clear_task(content):
                            use_agent = True
                        elif not _is_casual_chat(content):
                            # 安全兜底：过往/回忆类问题 + 无任务动词 → 强制聊天，不调用 LLM 分类器
                            content_lower = content.lower()
                            is_past_question = any(m in content_lower for m in _PAST_MARKERS) or content.strip().endswith("吗") or "？" in content
                            if is_past_question and not any(w in content_lower for w in _FS_WORDS) and not _has_task_verb(content):
                                logger.info("[路由] 检测为过往/回忆类问题，跳过 Agent: %s", content[:50])
                                use_agent = False
                            else:
                                intent = await _classify_intent(provider, content)
                                use_agent = (intent == "task")

                    if use_agent:
                        ws_path = msg.get("workspacePath")
                        if ws_path:
                            try:
                                target = _os.path.expanduser(ws_path)
                                _os.chdir(target)
                                logger.info("Agent CWD 切换至: %s", _os.getcwd())
                                # 将用户选择的工作空间动态注册到沙箱白名单（自动清空上次的）
                                from app.core.agent.sandbox import register_workspace
                                register_workspace(ws_path)
                            except FileNotFoundError:
                                # 文件夹被用户手动删除 → 回退 + 前端提醒
                                logger.warning("CWD 路径不存在: %s — 可能已被手动删除", ws_path)
                                await _send_json(ws, {
                                    "type": "error",
                                    "message": f"工作空间路径不存在: {ws_path}，已回退到项目根目录。请在左侧栏重新选择或删除该空间。"
                                })
                                try:
                                    _os.chdir(_original_cwd)
                                except Exception:
                                    pass
                                from app.core.agent.sandbox import clear_runtime_paths
                                clear_runtime_paths()
                                ws_path = ""
                            except Exception as e2:
                                logger.warning("CWD 切换失败 (%s): %s，回退至原始目录", ws_path, e2)
                                try:
                                    _os.chdir(_original_cwd)
                                except Exception:
                                    pass
                                # CWD 切换失败时也清空运行时路径，避免残留
                                from app.core.agent.sandbox import clear_runtime_paths
                                clear_runtime_paths()
                                ws_path = ""
                        else:
                            # 无工作空间 → 回退到项目根目录，同时清空运行时白名单
                            if _os.getcwd() != _original_cwd:
                                _os.chdir(_original_cwd)
                            from app.core.agent.sandbox import clear_runtime_paths
                            clear_runtime_paths()
                        from app.core.agent.loop import run_agent_loop, _write_checkpoint, _cleanup_checkpoint
                        from app.config import get_settings as _get_settings

                        # ── Task Summary → 记忆管道回调 ──
                        async def _on_task_done(summary: str):
                            """Agent 任务完成后，将摘要注入本轮记忆抽取管线。"""
                            # 将摘要追加为系统消息，供后续 extract_memories 使用
                            history.append(LLMMessage(
                                role="system",
                                content=f"[Agent 任务摘要] {summary}"
                            ))

                        task = None
                        task_id = ""
                        request_cancel = asyncio.Event()

                        async def _ws_cancel_watcher():
                            try:
                                raw = await ws.receive_text()
                                msg = json.loads(raw)
                                if msg.get("type") == "cancel":
                                    request_cancel.set()
                            except (WebSocketDisconnect, json.JSONDecodeError, Exception):
                                pass

                        agent_watcher = asyncio.create_task(_ws_cancel_watcher())
                        try:
                            task = await run_agent_loop(
                                content, provider, ws, mode=mode, cwd=ws_path or "",
                                cancel_event=request_cancel, on_task_complete=_on_task_done,
                                session_history=history[:-1][-12:] if len(history) > 1 else [],
                                lore_only=locals().get("lore_only", False),
                            )
                        except Exception as agent_exc:
                            logger.error("Agent 循环异常: %s: %s", type(agent_exc).__name__, agent_exc)
                            if task and task.get("id"):
                                try:
                                    _write_checkpoint(task, _get_settings())
                                except Exception:
                                    pass
                            raise
                        finally:
                            if not agent_watcher.done():
                                agent_watcher.cancel()
                        response_text = task.get("result", "任务执行完成。") if task else "任务执行异常。"
                        response_text = _strip_action_desc(response_text)

                        # ── Agent 模式统一情绪抽取、标签擦除与表情包逻辑 ──
                        # 1. 剥离模型输出中的思维链思考块 (如 <think>...</think> 或 【thinking】...)
                        response_text = re.sub(
                            r'(?:<think>[\s\S]*?</think>|【thinking】[\s\S]*?(?:【/thinking】|\n\n)|【思考】[\s\S]*?(?:【/思考】|\n\n))',
                            '', response_text
                        ).strip()

                        # 2. 正则提取并干净擦除文本中所有的【情绪：xxx】标签（无论中英文、无论位于开头/中间/末尾）
                        emo_pattern = r'【(?:情绪[：:])?\s*([\w\u4e00-\u9fff-]+)\s*】|\[emo:\s*([\w\u4e00-\u9fff-]+)\s*\]'
                        emo_match = re.search(emo_pattern, response_text)
                        raw_emotion = None
                        if emo_match:
                            raw_emotion = next(g for g in emo_match.groups() if g)
                            # 全面强力擦除所有 【情绪：xxx】 或 【happy】 标签文本
                            response_text = re.sub(emo_pattern, '', response_text).strip()

                        # 3. 情绪检测与表情包获取
                        INVALID_EMOTIONS = {"thinking", "思考", "reasoning", "thought", "分析", "neutral"}
                        if raw_emotion and raw_emotion.lower() not in INVALID_EMOTIONS:
                            emotion_label = raw_emotion
                        else:
                            emotion_label = _detect_emotion(response_text, user_text=content)

                        # 清洗 Emoji 与舞台动作指示
                        response_text = _strip_emoji(response_text)
                        response_text = re.sub(r'【[^】]*】', '', response_text).strip()

                        # 表情包挑选与匹配
                        from app.core.memes.scanner import get_meme_selector
                        selector = get_meme_selector()
                        match_text = f"{response_text} {content}"
                        raw_meme_path = selector.pick(emotion_label, text=match_text)
                        meme_url = _resolve_meme_url(raw_meme_path)

                        history.append(LLMMessage(role="assistant", content=response_text))
                        memory_manager.add_message("assistant", response_text)
                        if current_session_id:
                            try:
                                memory_manager.save_chat_message(
                                    current_session_id, "assistant", response_text, mode, emotion_label)
                            except Exception as e2:
                                logger.error("保存Agent结果失败: %s", e2)

                        await _send_json(ws, {"type": "emotion", "label": emotion_label})
                        await _send_json(ws, {"type": "done", "message": {
                            "id": f"msg-{uuid.uuid4().hex[:8]}", "role": "assistant",
                            "content": response_text, "emotion": emotion_label, "mode": mode,
                            "meme": meme_url,
                            "createdAt": int(time.time() * 1000),
                        }})
                        if voice_enabled and response_text.strip():
                            asyncio.create_task(_send_tts(response_text))
                        _trigger_extraction_if_needed(
                            memory_manager, provider, history[-6:], mode, ws, source="agent"
                        )
                        continue


                    # 普通 LLM 流式生成
                    full_content: list[str] = []
                    full_thinking: list[str] = []
                    had_error = False
                    error_message = ""
                    was_cancelled = False
                    token_usage_data: dict | None = None
                    stream_start_ms = int(time.time() * 1000)  # 记录生成开始时间
                    request_cancel = asyncio.Event()

                    async def _ws_cancel_watcher():
                        """读取一条 WS 消息，若是 cancel 则置位 request_cancel。"""
                        try:
                            raw = await ws.receive_text()
                            msg = json.loads(raw)
                            if msg.get("type") == "cancel":
                                request_cancel.set()
                        except (WebSocketDisconnect, json.JSONDecodeError, Exception):
                            pass

                    ws_watcher = asyncio.create_task(_ws_cancel_watcher())
                    try:
                        async for token in provider.generate_stream(messages):
                            # 检查是否触发了取消
                            if request_cancel.is_set():
                                was_cancelled = True
                                # 推送已生成的部分内容作为最后一句话
                                response_text = "".join(full_content) or "[已终止]"
                                await _send_json(ws, {"type": "done", "message": {
                                    "id": f"msg-{uuid.uuid4().hex[:8]}", "role": "assistant",
                                    "content": response_text, "emotion": "neutral", "mode": mode,
                                    "createdAt": int(time.time() * 1000),
                                }})
                                break
                            if token.startswith(_PREFIX_TOKEN):
                                delta = token[len(_PREFIX_TOKEN):]
                                full_content.append(delta)
                                await _send_json(ws, {"type": "token", "delta": delta})
                            elif token.startswith(_PREFIX_THINKING):
                                delta = token[len(_PREFIX_THINKING):]
                                full_thinking.append(delta)
                                if mode == "work":
                                    await _send_json(ws, {"type": "thinking", "delta": delta})
                            elif token.startswith("USAGE:"):
                                # 捕获 Token 用量（流末尾）
                                try:
                                    token_usage_data = json.loads(token[len("USAGE:"):])
                                except json.JSONDecodeError:
                                    pass
                            elif token.startswith(_PREFIX_ERROR):
                                err_msg = token[len(_PREFIX_ERROR):]
                                await _send_json(ws, {"type": "error", "message": err_msg})
                                had_error = True
                                error_message = err_msg
                                break
                    except Exception as e2:
                        err_msg = f"服务端内部错误: {type(e2).__name__}: {e2}"
                        try:
                            await _send_json(ws, {"type": "error", "message": err_msg})
                        except Exception:
                            pass
                        had_error = True
                        error_message = err_msg
                    finally:
                        if not ws_watcher.done():
                            ws_watcher.cancel()

                    # 取消后跳过后续处理
                    if was_cancelled:
                        continue

                    if had_error:
                        history.pop()
                        await _send_json(ws, {"type": "done", "message": {
                            "id": f"err-{uuid.uuid4().hex[:8]}", "role": "assistant",
                            "content": f"[错误] {error_message}", "emotion": "neutral", "mode": mode,
                            "createdAt": int(time.time() * 1000),
                        }})
                        continue

                    # 情绪解析 + 表情包
                    from app.core.memes.scanner import get_meme_selector
                    response_text = "".join(full_content)

                    # 未解锁日常模式：第二重安全熔断，绝对防死未捕获的 web_search 控制文本打印给用户
                    if mode == "daily" and not daily_unlocked:
                        if "web_search" in response_text or '{"query":' in response_text:
                            logger.warning("[日常模式] 拦截泄露的工具控制文本: %s", response_text[:100])
                            response_text = "唔…我现在还没有开启联网搜索功能呢。要不要在设置里开启解禁，或者切换到工作模式试试看？"

                    # 1. 剥离模型输出中的思维链思考块 (如 <think>...</think> 或 【thinking】...【/thinking】 或 【思考】...)
                    response_text = re.sub(
                        r'(?:<think>[\s\S]*?</think>|【thinking】[\s\S]*?(?:【/thinking】|\n\n)|【思考】[\s\S]*?(?:【/思考】|\n\n))',
                        '', response_text
                    ).strip()

                    emo_pattern = (
                        r'(?:【情绪[：:]\s*([\w\u4e00-\u9fff]+)\s*】'
                        r'|\[emo:\s*([\w\u4e00-\u9fff]+)\s*\]'
                        r'|\(?情绪[：:]\s*([\w\u4e00-\u9fff]+)\s*\)?'
                        r'|【\s*([\w\u4e00-\u9fff]+)\s*】)'
                    )
                    emo_match = re.search(emo_pattern, response_text)
                    raw_emotion = None
                    if emo_match:
                        raw_emotion = next(g for g in emo_match.groups() if g)
                        # 清除开头和末尾的情绪标签（LLM 可能不按 prompt 要求放在末尾）
                        response_text = re.sub(
                            r'^\s*(?:【情绪[：:]\s*\w+\s*】|\[emo:\s*\w+\s*\]|情绪[：:]\s*\w+|【\s*\w+\s*】)\s*\n?',
                            '', response_text)
                        response_text = re.sub(
                            r'\n?\s*(?:【情绪[：:]\s*\w+\s*】|\[emo:\s*\w+\s*\]|情绪[：:]\s*\w+|【\s*\w+\s*】)\s*$',
                            '', response_text).rstrip()

                    # 过滤非法非情绪控制词（如 thinking, 思考等），防止错认成情绪名称导致表情包消失
                    INVALID_EMOTIONS = {"thinking", "思考", "reasoning", "thought", "分析"}
                    if raw_emotion and raw_emotion.lower() not in INVALID_EMOTIONS:
                        emotion_label = raw_emotion
                    else:
                        emotion_label = _detect_emotion(response_text, user_text=content)

                    # 在发送前端之前剥离 Unicode emoji（表情包系统独立，不受影响）
                    response_text = _strip_emoji(response_text)
                    # 兜底剥离舞台指示 / 动作描写（模型可能绕过 prompt 限制）
                    response_text = _strip_action_desc(response_text)
                    # 最终兜底剥离残留的裸情绪标签（如 LLM 违规输出 【neutral】）
                    response_text = re.sub(r'【[^】]*】', '', response_text).strip()

                    # ═══════════════════════════════════════════════════════
                    # 立即启动 TTS（异步非阻塞），与后续 emotion/done/meme 并行
                    # 文字已先到前端，TTS 在后台推理，用户感知延迟最小
                    # ═══════════════════════════════════════════════════════
                    if voice_enabled and response_text.strip():
                        asyncio.create_task(_send_tts(response_text))

                    await _send_json(ws, {"type": "emotion", "label": emotion_label})

                    selector = get_meme_selector()
                    match_text = f"{response_text} {content}"
                    meme_path = selector.pick(emotion_label, text=match_text)
                    if not meme_path and mode == "work":
                        meme_path = selector.pick("work", text=match_text)
                    meme_url = _resolve_meme_url(meme_path)

                    done_msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                    await _send_json(ws, {"type": "done", "message": {
                        "id": done_msg_id, "role": "assistant",
                        "content": response_text, "emotion": emotion_label, "mode": mode,
                        "meme": meme_url,
                        "createdAt": int(time.time() * 1000),
                    }})

                    # 发送 Token 消耗明细（CodeBuddy 风格细分）
                    if token_usage_data:
                        elapsed_ms = int(time.time() * 1000) - stream_start_ms
                        prompt = token_usage_data.get("prompt_tokens", 0)
                        completion = token_usage_data.get("completion_tokens", 0)
                        cached = token_usage_data.get("cached_tokens", 0)
                        api_reasoning = token_usage_data.get("reasoning_tokens", 0)

                        # ── 推理: 优先 API 值，否则按字符比例估算 ──────────
                        reasoning = api_reasoning  # API 提供的准确值
                        if reasoning == 0:
                            thinking_chars = token_usage_data.get("thinking_chars", 0)
                            reply_chars = token_usage_data.get("reply_chars", 0)
                            total_chars = thinking_chars + reply_chars
                            if total_chars > 0:
                                # 按字符比例从 completion_tokens 中分配
                                ratio = thinking_chars / total_chars
                                reasoning = round(completion * ratio)
                                # 防止极端值（ratio≈1 时 reply=0 看起来异常）
                                if reasoning >= completion and completion > 0:
                                    reasoning = completion - 1

                        reply_tokens = max(0, completion - reasoning)

                        await _send_json(ws, {
                            "type": "token_usage",
                            "usage": {
                                "promptTokens": prompt,
                                "completionTokens": completion,
                                "totalTokens": token_usage_data.get("total_tokens", 0),
                                "cachedTokens": cached,
                                "cacheWriteTokens": 0,  # 当前 API 不直接提供
                                "reasoningTokens": reasoning,
                                "replyTokens": reply_tokens,
                                "elapsedMs": elapsed_ms,
                            },
                            "messageId": done_msg_id,
                        })

                    if meme_path:
                        from pathlib import Path
                        p = Path(meme_path)
                        if p.exists() and p.is_file():
                            suffix = p.suffix.lower().lstrip(".")
                            mime = "image/jpeg" if suffix == "jpg" else f"image/{suffix}"
                            try:
                                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                                data_url = f"data:{mime};base64,{b64}"
                                await _send_json(ws, {"type": "meme", "id": f"meme-{uuid.uuid4().hex[:8]}",
                                                       "data": data_url, "createdAt": int(time.time() * 1000)})
                            except Exception as e2:
                                logger.error("表情包读取失败: %s", e2)

                    history.append(LLMMessage(role="assistant", content=response_text))
                    memory_manager.add_message("assistant", response_text)

                    if current_session_id:
                        try:
                            memory_manager.save_chat_message(
                                current_session_id, "assistant", response_text, mode, emotion_label)
                            from app.core import db as _db2
                            sess = _db2.get_session(current_session_id)
                            if sess and sess.get("title") in ("新会话", "默认会话"):
                                user_msgs = _db2.load_history(current_session_id, limit=5)
                                first_user = next((hm for hm in user_msgs if hm["role"] == "user"), None)
                                if first_user:
                                    title = first_user["content"][:20]
                                    if len(first_user["content"]) > 20:
                                        title += "…"
                                    _db2.update_session_title(current_session_id, title)
                        except Exception as e2:
                            logger.error("保存助手消息失败: %s", e2)

                    _trigger_extraction_if_needed(
                        memory_manager, provider, history[-6:], mode, ws, source="chat"
                    )

                # ─── 模式切换 ───────────────────────────────────────
                elif msg_type == "mode_switch":
                    new_mode = msg.get("mode", "daily")
                    if new_mode not in ("daily", "work"):
                        continue
                    memory_manager.switch_namespace(new_mode)
                    mode = new_mode
                    settings.mode.current = new_mode

                    # 推送过场台词（firefly-skill 融合：变身/解甲台词）
                    transition_line = persona.transition_lines.get(
                        "to_work" if new_mode == "work" else "to_daily", ""
                    )
                    if transition_line:
                        await _send_json(ws, {
                            "type": "transition_line",
                            "line": transition_line,
                            "to_mode": new_mode,
                        })

                    await _send_mode_config(ws, new_mode)

                # ─── 语音开关 ───────────────────────────────────────
                elif msg_type == "voice_toggle":
                    voice_enabled = msg.get("enabled", True)
                    await _send_json(ws, {"type": "voice_toggled", "enabled": voice_enabled})

                # ─── 手动触发主动聊天（测试用）───────────────────
                elif msg_type == "trigger_proactive":
                    if not (mode == "daily" and pc_config and pc_config.enabled):
                        await _send_json(ws, {
                            "type": "proactive_speech",
                            "content": "主动聊天仅在日常模式下可用，且需在设置中开启",
                            "source": "manual_trigger",
                            "motion": "greet",
                            "expression": "smile",
                        })
                    else:
                        live_settings = get_settings()
                        p = LLMProviderRegistry.create(
                            live_settings.llm.provider, api_key=live_settings.llm.api_key,
                            base_url=live_settings.llm.base_url, model=live_settings.llm.model,
                            temperature=0.85, max_tokens=256, enable_thinking=False,
                        )
                        recalled = await memory_manager.recall("", mode, top_k=3) if settings.memory.long_term_enabled else []
                        content = await active_concern.generate_proactive_content(
                            p, mode=mode, idle_minutes=live_settings.proactive_chat.idle_minutes,
                            recalled_memories=recalled,
                        )
                        if content:
                            await _send_json(ws, {
                                "type": "proactive_speech",
                                "content": content,
                                "source": "manual_trigger",
                                "motion": "greet",
                                "expression": "smile",
                            })
                            # 重置空闲计时器
                            if idle_engine:
                                idle_engine.reset()
                        else:
                            content = random.choice(_PROACTIVE_FALLBACKS)
                            content = _strip_action_desc(content)
                            await _send_json(ws, {
                                "type": "proactive_speech",
                                "content": content,
                                "source": "manual_trigger",
                                "motion": "greet",
                                "expression": "smile",
                            })
                        # 持久化：加入 LLM 上下文 + 写入聊天记录
                        history.append(LLMMessage(role="assistant", content=content))
                        memory_manager.add_message("assistant", content)
                        sid = msg.get("sessionId") or current_session_id
                        if sid:
                            try:
                                memory_manager.save_chat_message(
                                    sid, "assistant", content, mode, "neutral")
                            except Exception as e2:
                                logger.error("保存主动聊天消息失败: %s", e2)
                        from app.core import db as _db4
                        # 手动/测试触发不计入“当日主动聊天上限”（trigger 与空闲触发区分）
                        _db4.add_concern("proactive_test", content or "手动触发（无内容）", mode)

                # ─── 清空对话 ───────────────────────────────────────
                elif msg_type == "reset":
                    history.clear()
                    memory_manager.clear_short_term()
                    if current_session_id:
                        try:
                            from app.core import db as _db3
                            _db3.clear_history(current_session_id)
                        except Exception as e2:
                            logger.error("清空历史失败: %s", e2)

                # ─── 审批回复 ───────────────────────────────────────
                elif msg_type == "approval_response":
                    step_id = msg.get("stepId", "")
                    approved = msg.get("approved", False)
                    if step_id:
                        resolve_approval(step_id, approved)

            except Exception as e:
                # 兜底保护：任何消息处理异常不崩 WS
                logger.exception("消息处理异常: %s: %s", type(e).__name__, e)
                try:
                    await _send_json(ws, {"type": "error", "message": f"服务端异常: {e}"})
                except Exception:
                    break  # WS 已断，退出循环

    except WebSocketDisconnect:
        pass
    finally:
        # 清理空闲引擎
        if idle_engine:
            idle_engine.stop()
            if _active_idle_engine is idle_engine:
                _active_idle_engine = None


def update_active_idle_engine_config(
    idle_minutes=None,
    enabled=None,
    quiet_hours_start=None,
    quiet_hours_end=None,
    daily_limit=None,
) -> bool:
    """保存设置后热更新当前活动的空闲引擎，使新配置立即生效（无需重连 WS）。

    返回是否成功更新了正在运行的引擎；若引擎尚未创建（无 WS 连接），
    则下次连接会自动读取新配置（get_settings 已 cache_clear）。
    """
    global _active_idle_engine
    engine = _active_idle_engine
    if engine is None:
        return False
    kwargs = {}
    if idle_minutes is not None:
        kwargs["idle_seconds"] = int(idle_minutes) * 60
    if quiet_hours_start is not None:
        kwargs["quiet_hours_start"] = int(quiet_hours_start)
    if quiet_hours_end is not None:
        kwargs["quiet_hours_end"] = int(quiet_hours_end)
    if daily_limit is not None:
        kwargs["daily_limit"] = int(daily_limit)
    if kwargs:
        engine.update_config(**kwargs)
    # enabled 控制引擎启停：关闭则停止，重新开启且未运行时启动
    if enabled is False:
        engine.stop()
    elif enabled is True and not engine.is_running:
        asyncio.create_task(engine.start())
    return True


def _try_record_instant_memories(content: str, mgr, ws=None):
    """检测强记忆信号（偏好 / 人际关系 / 社交与出行事件）→ 零延迟实时捕获并写入长期记忆。"""
    import re
    cleaned = re.sub(r"^(?:我说|我跟你说|我之前说过)[，,\s]*", "", content).strip()
    
    tasks = []

    # 1. 强偏好模式（非常喜欢/最爱/超爱/特别喜欢）
    m_pref = re.search(
        r"(?:非常喜欢|特别喜欢|最喜欢|超(?:爱|喜欢)|好喜欢)(?:的?是?|了?)?([^\s，。！？,!?]{2,20}?)(?:[，。！？,!?\s]|$)",
        cleaned
    )
    if m_pref:
        entity = m_pref.group(1).strip()
        skip_words = {"一下", "一个", "什么", "哪种", "哪个", "这件", "那件", "这个", "那个"}
        if entity and len(entity) >= 2 and entity not in skip_words:
            memory_text = f"用户非常喜欢{entity}"
            tasks.append({
                "content": memory_text,
                "type": "preference",
                "namespace": "shared_profile",
                "topic_default": "entertainment_hobby",
                "entity": entity,
                "confidence": 0.95,
                "log_tag": "强偏好",
            })

    # 2. 强人际关系模式（小美是我的姐姐 / 小刚是我的朋友）
    m_rel = re.search(
        r"([^\s，。！？,!?]{2,10}?)\s*是(?:我|用户)的\s*(爸爸|妈妈|姐姐|妹妹|哥哥|弟弟|朋友|同学|室友|同事|领导|爱人|老婆|老公)",
        cleaned
    )
    if m_rel:
        person_name = m_rel.group(1).strip()
        relation_role = m_rel.group(2).strip()
        if person_name and relation_role and len(person_name) >= 2:
            memory_text = f"{person_name}是用户的{relation_role}"
            family_roles = {"爸爸", "妈妈", "姐姐", "妹妹", "哥哥", "弟弟", "爱人", "老婆", "老公"}
            topic = "relationship_family" if relation_role in family_roles else "relationship_friend"
            tasks.append({
                "content": memory_text,
                "type": "relationship",
                "namespace": "shared_profile",
                "topic_default": topic,
                "entity": person_name,
                "confidence": 0.95,
                "log_tag": "强人际关系",
            })

    # 3. 强社交与出行事件模式（我昨天和小刚一起去重庆旅游了 / 和小美去游泳了）
    m_event = re.search(
        r"(?:我|昨天|前天|上周|最近)?\s*(?:和|同|与)\s*([^\s，。！？,!?]{2,10}?)\s*(?:一起)?(?:去|到)?\s*([^\s，。！？,!?]{2,20}?)(?:旅游|出差|玩|看电影|吃烧烤|游泳|爬山|逛街|吃饭)(?:了|过)?(?:[，。！？,!?\s]|$)",
        cleaned
    )
    if m_event:
        companion = m_event.group(1).strip()
        destination = m_event.group(2).strip()
        if companion and len(companion) >= 2:
            act_m = re.search(r"(旅游|出差|玩|看电影|吃烧烤|游泳|爬山|逛街|吃饭)", cleaned)
            action_str = act_m.group(1) if act_m else "游玩"
            dest_str = f"去{destination}" if destination else ""
            memory_text = f"用户和{companion}一起{dest_str}{action_str}"
            topic = "event_travel" if "旅游" in action_str or "出差" in action_str else "event_social"
            tasks.append({
                "content": memory_text,
                "type": "event",
                "namespace": "daily_life",
                "topic_default": topic,
                "entity": companion,
                "confidence": 0.95,
                "log_tag": "强社交事件",
            })

    if not tasks:
        return

    async def _do_write():
        from app.core.memory.personal import _detect_topic, _is_lore_leak
        saved_count = 0
        for item in tasks:
            mem_text = item["content"]
            if _is_lore_leak(mem_text):
                logger.info("[memory] 跳过 lore 泄漏: %s", mem_text[:40])
                continue
            det_topic, _det_ent = _detect_topic(mem_text)
            topic = det_topic if det_topic and det_topic != "general_preference" else item["topic_default"]
            ok = await mgr.personal.write_long_term(
                content=mem_text,
                metadata={"source": "instant_extraction", "type": item["type"]},
                confidence=item["confidence"],
                namespace=item["namespace"],
                topic=topic,
                entity=item["entity"],
            )
            if ok:
                saved_count += 1
                logger.info("[memory] 实时记录%s: %s (topic=%s, ns=%s)", item["log_tag"], mem_text, topic, item["namespace"])
        if saved_count > 0 and ws:
            try:
                await _send_json(ws, {"type": "memory_updated", "count": saved_count})
            except Exception:
                pass

    asyncio.create_task(_do_write())


def _trigger_extraction_if_needed(mgr, provider, recent_messages, mode, ws=None, source="chat"):
    live_settings = get_settings()
    count = mgr.personal._message_count
    interval = live_settings.memory.memory_extraction_interval
    threshold = max(2, interval)
    lt_enabled = live_settings.memory.long_term_enabled
    should_ext = mgr.should_extract

    logger.info(
        "[chat] 检查记忆提取条件 [%s]: count=%d threshold=%d lt_enabled=%s should_extract=%s",
        source, count, threshold, lt_enabled, should_ext
    )
    # work 模式不提取记忆（纯任务指令，无个人价值）
    if source == "agent" and mode == "work":
        return

    if should_ext and lt_enabled:
        logger.info(
            "[chat] 触发记忆提取 [%s]: count=%d interval=%d lt_enabled=%s",
            source, count, interval, lt_enabled
        )
        # 为后台结构化 JSON 提取创建关闭 thinking 的独立 Provider，提升响应速度并防止 <think> 标签干扰
        extraction_provider = LLMProviderRegistry.create(
            live_settings.llm.provider,
            api_key=live_settings.llm.api_key,
            base_url=live_settings.llm.base_url,
            model=live_settings.llm.model,
            temperature=0.3,
            max_tokens=1024,
            enable_thinking=False,
        )
        asyncio.create_task(_safe_extract(mgr, extraction_provider, recent_messages, mode, ws))


async def _safe_extract(mgr, provider, recent_messages, mode, ws=None):
    try:
        saved = await mgr.extract_memories(provider, recent_messages, mode)
        logger.info("[chat] 成功抽取 %d 条长期记忆", saved)
        if ws and saved > 0:
            try:
                await _send_json(ws, {"type": "memory_updated", "count": saved})
            except Exception as e_ws:
                logger.warning("[chat] 发送 memory_updated WS 消息失败: %s", e_ws)
    except Exception as e:
        logger.exception("后台记忆抽取异常: %s", e)

