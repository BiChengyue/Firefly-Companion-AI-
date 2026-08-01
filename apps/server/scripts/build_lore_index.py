"""离线剧情索引构建 — 方案 B（第二十八阶段）。

将四类信源清洗、切块后写入 SQLite（FTS5 全文索引 + ONNX 向量）：
- L0: data/knowledge/firefly_lore.md（流萤亲历记忆，按 ## 块整块入库）
- L0: data/knowledge/curated_cards/*.md（高频幻觉场景精选卡片）
- L1~L2: resources/流萤/**（官方原文：主线剧情/角色故事/语音/短信/视频）
- L1~L4: resources/hsrchat/references/wiki/**（角色/语音/任务/NPC）

用法（项目根目录执行）:
    python apps/server/scripts/build_lore_index.py            # 全量构建（含向量）
    python apps/server/scripts/build_lore_index.py --no-vectors  # 只建 FTS，跳过向量
    python apps/server/scripts/build_lore_index.py --force    # 忽略 hash 强制重建
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ── 路径 ──
_SCRIPT = Path(__file__).resolve()
PROJECT_ROOT = _SCRIPT.parents[3]          # apps/server/scripts → 项目根
SERVER_ROOT = _SCRIPT.parents[1]           # apps/server
WIKI_ROOT = PROJECT_ROOT / "resources" / "hsrchat" / "references" / "wiki"
LORE_MD = PROJECT_ROOT / "data" / "knowledge" / "firefly_lore.md" 
CARDS_DIR = PROJECT_ROOT / "data" / "knowledge" / "curated_cards"
FIREFLY_OFFICIAL_ROOT = PROJECT_ROOT / "resources" / "流萤"
WORLD_LORE_DIR = PROJECT_ROOT / "data" / "knowledge"
DB_PATH = PROJECT_ROOT / "data" / "lore_index.db"

# 文件名 → 世界锚点名称映射（用于 chunk 标记）
_WORLD_NAME_MAP = {
    "two_phase_paradise_lore.md": "二相乐园（哈托彼亚）",
    "amphoreus_lore.md": "翁法罗斯",
    "herta_space_station_lore.md": "黑塔空间站",
    "jarilo_vi_lore.md": "雅利洛-VI（贝洛伯格）",
    "xianzhou_luofu_lore.md": "仙舟「罗浮」",
}

# world_lore 实体标签扫描用：与 hsr_lore.py _ENTITY_ALIASES 的值集保持同步
# 注意：本表是 _ENTITY_ALIASES 的去重值超集 — 新增角色需两边同步添加
_WORLD_ENTITY_NAMES = {
    # ── 角色 · 星核猎手 ──
    "流萤", "萨姆", "银狼", "卡芙卡", "刃", "艾利欧",
    # ── 角色 · 星穹列车 ──
    "开拓者", "三月七", "丹恒", "姬子", "瓦尔特",
    # ── 角色 · 雅利洛-VI ──
    "布洛妮娅", "希儿", "杰帕德", "克拉拉", "娜塔莎", "桑博",
    "可可利亚", "希露瓦", "史瓦罗", "虎克", "卢卡", "佩拉", "瓦切", "帕斯卡",
    # ── 角色 · 仙舟罗浮 ──
    "符玄", "景元", "镜流", "丹枢", "白露", "彦卿", "罗刹",
    "丹枫", "停云", "灵砂", "飞霄", "椒丘", "貊泽", "云璃",
    "藿藿", "桂乃芬", "呼雷", "白珩", "应星", "驭空", "青雀",
    "幻胧", "彩翼", "晴霓", "真德林", "尾巴大爷",
    # ── 仙舟派系/概念 ──
    "持明族", "狐人族", "步离人", "仙舟联盟", "魔阴身", "岁阳",
    "十王司", "丹鼎司", "天舶司", "丰饶民",
    # ── 角色 · 匹诺康尼 ──
    "星期日", "知更鸟", "花火", "黄泉", "翡翠", "加拉赫", "大丽花", "黑天鹅",
    "米莎", "钟表匠",
    # ── 角色 · 黑塔空间站 ──
    "黑塔", "艾丝妲", "阿兰", "螺丝咕姆", "阮·梅", "真理医生",
    "斯蒂芬", "阿弗利特", "冥火大公",
    # ── 空间站概念 ──
    "模拟宇宙", "末日兽", "奇物", "以太涂鸦", "碎星王虫",
    # ── 角色 · 翁法罗斯 ──
    "白厄", "卡厄斯兰那", "阿格莱雅", "万敌", "迈德谟斯",
    "遐蝶", "卡斯托里斯", "那刻夏", "阿那克萨戈拉斯",
    "风堇", "雅辛忒丝", "赛飞儿", "赛法利娅",
    "海瑟音", "海列屈拉", "刻律德菈", "凯莉丝",
    "昔涟", "爱莉希雅", "缇宝", "缇里西庇俄丝",
    "缇宁", "缇安", "迷迷", "来古士", "盗火行者", "德谬歌", "长夜月",
    "皮西厄斯", "小伊卡",
    # ── 翁法罗斯概念 ──
    "泰坦", "火种", "逐火之旅", "黄金裔", "黑潮", "铁墓", "门扉",
    "死龙", "帝皇权杖", "识刻锚",
    # ── 角色 · 二相乐园 ──
    "隆介", "归寂", "绘世", "绯英", "虚照", "爻光", "不死途", "朽叶",
    "火花", "真珠", "幻太子", "鳄鱼侦探", "乔瓦尼", "啵啵娃",
    "九喵儿", "普狸策", "欧泊", "美亚", "素子",
    # ── 二相乐园概念 ──
    "幻月", "幻造种", "告死魔", "风化诅咒", "倏忽血肉", "差分宇宙",
    # ── 角色 · 星际和平公司 ──
    "托帕", "砂金",
    # ── 其他角色 ──
    "乱破", "渔阳",
    # ── 派系/组织 ──
    "星核猎手", "格拉默", "星穹列车", "反物质军团", "药王秘传",
    "云骑军", "银鬃铁卫", "地火", "流光忆庭", "假面愚者",
    "星际和平公司", "博识学会", "纯美骑士团", "绘世家族",
    # ── 地点/世界 ──
    "匹诺康尼", "翁法罗斯", "二相乐园", "贝洛伯格", "雅利洛-VI",
    "黑塔空间站", "仙舟罗浮", "仙舟朱明", "仙舟曜青",
    # ── 星神/命途 ──
    "纳努克", "阿基维利", "岚", "克里珀", "药师",
    "绝灭大君", "幻胧", "星啸", "焚风", "铁墓",
    # ── 核心概念 ──
    "黑潮", "黄金裔", "幻造种", "告死魔", "风化诅咒",
    "造物引擎", "建木", "鳞渊境", "永冬岭",
    # ── 雅利洛-VI 补充 ──
    "大守护者", "帕斯卡",
    # ── 星际和平公司 ──
    "欧泊", "美亚", "斯科特", "林登斯科特", "孤狼",
    # ── 星穹列车 ──
    "帕姆",
}

sys.path.insert(0, str(SERVER_ROOT))

# 分类目录 → (category, priority)
CATEGORY_MAP = {
    "角色语音": ("character_voice", 1),
    "角色": ("character", 1),
    "开拓任务": ("story_main", 2),
    "同行任务": ("story_companion", 3),
    "开拓续闻": ("story_side", 4),
    "冒险任务": ("story_adventure", 4),
    "NPC": ("npc", 4),
    "书籍": ("book", 3),
}

CHUNK_TARGET = 500   # 目标块大小（字符）
CHUNK_MAX = 750      # 硬上限

# 战斗/系统类语音 — 纯游戏机制文本，对剧情问答是噪声，构建时排除
VOICE_SKIP = re.compile(
    r"战斗开始|回合开始|终结技|受击|闪避|重击|上场|下场|返回城镇|道别|问候|"
    r"角色满级|晋阶|突破|解谜|宝箱|战技|普攻|天赋|秘技|无法战斗|重新上场|"
    r"获得道具|队伍编成|战斗胜利|弱点击破|危险规避|附加能力|发现敌方|"
    r"回合限制|拾取|升级|抽卡|入队|战斗中|战败"
)


# ══════════════════ 清洗 ══════════════════

def clean_wikitext(text: str) -> str:
    t = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    t = re.sub(r"\{\{任务描述\|([^|}]*)\}\}", r"\1", t)
    t = re.sub(r"\{\{折叠\|标题=([^|}]*)\|内容=", r"\1\n", t)
    t = re.sub(r"\{\{颜色\|[^|}]*\|([^|}]*)\}\}", r"\1", t)
    t = re.sub(r"\{\{注音\|([^|}]*)\|[^}]*\}\}", r"\1", t)
    t = re.sub(r"\{\{图标\|[^}]*\}\}", "", t)
    # NPC 机制与菜单废话过滤
    t = re.sub(r"\[(?:买卖|商店|离开|功能|对话|合成|强化)\]", "", t)
    t = re.sub(r"\|\s*(?:对话选项|功能|菜单)\s*=\s*[^\n]*", "", t)
    # 剧情选项模板：保留选项文本
    t = re.sub(r"\{\{剧情选项[^}]*\}\}", "", t)
    t = re.sub(r"\{\{[^{}]+\}\}", "", t)
    t = re.sub(r"\{\{[^{}]+\}\}", "", t)  # 二次清理嵌套残留
    t = re.sub(r"</?[a-zA-Z][^>]*>", "", t)
    t = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"''+", "", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def parse_story(fpath: Path, category: str, priority: int) -> list[dict]:
    """任务/NPC 文件：剧情梗概单独成块 + 正文段落切块，支持星神/派系自动打标。"""
    raw = fpath.read_text(encoding="utf-8", errors="ignore")
    ff = 1 if has_firefly_marker(raw, fpath.name) else 0
    title = fpath.stem
    chunks: list[dict] = []

    m = re.search(r"\|\s*剧情梗概\s*=\s*(.+?)(?=\n\||\n\}\}|\Z)", raw, re.DOTALL)
    if m:
        summary = clean_wikitext(m.group(1))
        if len(summary) >= 30:
            chunks.append({
                "source": "wiki", "category": category, "priority": priority,
                "has_firefly": ff, "file": fpath.name,
                "title": f"{title}·剧情梗概",
                "text": summary[:CHUNK_MAX], "trigger": "",
            })

    body = clean_wikitext(raw)
    for c in chunk_paragraphs(body, title):
        if len(c) < 25 or re.search(r"^(?:买卖|离开|交易|强化|商店|\s)+$", c):
            continue
        chunk_cat = category
        if _AEON_KEYWORDS.search(c):
            chunk_cat = "lore_aeon"
        elif _FACTION_KEYWORDS.search(c):
            chunk_cat = "lore_faction"

        chunks.append({
            "source": "wiki", "category": chunk_cat, "priority": priority,
            "has_firefly": ff, "file": fpath.name, "title": title,
                "text": c, "trigger": "",
            })
    return chunks


def segment_for_fts(text: str) -> str:
    """中文按单字切、ASCII 按词切，空格连接 — 适配 FTS5 unicode61 分词。"""
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text)
    return " ".join(tokens)


def has_firefly_marker(raw: str, fname: str) -> bool:
    if "流萤" in fname or "萨姆" in fname:
        return True
    m = re.search(r"\|\s*出场人物\s*=\s*([^\n]*)", raw)
    if m and re.search(r"流萤|萨姆", m.group(1)):
        return True
    return False


# ══════════════════ 切块器 ══════════════════

def chunk_paragraphs(text: str, title: str) -> list[str]:
    """按段落聚合成 ~500 字块，超长段落按句子边界硬切。"""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > CHUNK_MAX:
            if buf:
                chunks.append(buf)
                buf = ""
            # 超长段落按句子切
            start = 0
            while start < len(p):
                end = min(start + CHUNK_TARGET, len(p))
                while end < len(p) and p[end] not in "。！？\n":
                    end += 1
                chunks.append(p[start:end + 1].strip())
                start = end + 1
            continue
        if len(buf) + len(p) + 1 > CHUNK_TARGET and buf:
            chunks.append(buf)
            buf = p
        else:
            buf = (buf + "\n" + p) if buf else p
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) >= 30]


def parse_firefly_lore() -> list[dict]:
    """L0：firefly_lore.md 按 ## 块整块入库（不打碎）。"""
    if not LORE_MD.exists():
        return []
    text = LORE_MD.read_text(encoding="utf-8")
    out: list[dict] = []
    for part in text.split("\n## "):
        part = part.strip()
        if not part or part.startswith("# 流萤的自身记忆"):
            continue
        if not part.startswith("#"):
            part = "## " + part
        title = part.splitlines()[0].lstrip("# ").strip()
        out.append({
            "source": "firefly_lore", "category": "memory", "priority": 0,
            "has_firefly": 1, "file": "firefly_lore.md", "title": title,
            "text": part, "trigger": "",
        })
    return out


def parse_world_lore() -> list[dict]:
    """L1：data/knowledge/*_lore.md（排除 firefly_lore.md），按 ## 块入库。

    每个 chunk 注入世界锚点【世界：XXX】+ 自动实体标签【关键角色：X、Y】，
    has_firefly=0（流萤不在场）、source=world_lore、priority=1。
    """
    out: list[dict] = []
    for fpath in sorted(WORLD_LORE_DIR.glob("*_lore.md")):
        if fpath.name == "firefly_lore.md":
            continue
        world_name = _WORLD_NAME_MAP.get(fpath.name)
        text = fpath.read_text(encoding="utf-8")
        # 尝试从文件首行标题提取世界名
        first_line = text.splitlines()[0] if text else ""
        m = re.match(r"#\s*(.+?)(?:回忆|史诗|与印象|与记忆).*", first_line)
        if m and not world_name:
            world_name = m.group(1).strip()
        if not world_name:
            world_name = fpath.stem
        anchor = f"【世界：{world_name}】"
        for part in text.split("\n## "):
            part = part.strip()
            if not part:
                continue
            if not part.startswith("#"):
                part = "## " + part
            title = part.splitlines()[0].lstrip("# ").strip()
            if title == world_name or title.startswith("本文档"):
                continue
            # 自动实体标签扫描
            found_entities = sorted(
                {e for e in _WORLD_ENTITY_NAMES if e in part},
                key=lambda x: len(x), reverse=True,
            )[:8]  # 最多 8 个，优先长名（更具体）
            entity_tag = ""
            if found_entities:
                entity_tag = f"【关键角色：{'、'.join(found_entities)}】\n"
            out.append({
                "source": "world_lore", "category": "world_knowledge", "priority": 1,
                "has_firefly": 0, "file": fpath.name, "title": f"{world_name}·{title}",
                "text": f"{anchor}\n{entity_tag}{part}", "trigger": "",
            })
    return out


def parse_curated_cards() -> list[dict]:
    """L0：精选卡片，整卡入库。支持首行 `触发: 正则` 强制命中。"""
    if not CARDS_DIR.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(CARDS_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8").strip()
        if not text:
            continue
        trigger = ""
        m = re.search(r"^触发[:：]\s*(.+)$", text, re.MULTILINE)
        if m:
            trigger = m.group(1).strip()
        # 向量编码只用「标题 + 触发问题模式」，避免元文本稀释语义
        qm = re.search(r"##\s*触发问题模式\s*\n(.+?)(?=\n##|\Z)", text, re.DOTALL)
        embed_src = f"{f.stem} {qm.group(1).strip()}" if qm else text[:300]
        out.append({
            "source": "curated", "category": "card", "priority": 0,
            "has_firefly": 1, "file": f.name, "title": f.stem,
            "text": text, "trigger": trigger, "embed": embed_src,
        })
    return out


def parse_character(fpath: Path) -> list[dict]:
    """角色页：介绍 + 角色故事1-4，各自成块。"""
    raw = fpath.read_text(encoding="utf-8", errors="ignore")
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    name = fpath.stem
    chunks: list[dict] = []

    def _add(label: str, body: str):
        body = clean_wikitext(body)
        if len(body) < 20:
            return
        for i, c in enumerate(chunk_paragraphs(body, label)):
            chunks.append({
                "source": "wiki", "category": "character", "priority": 1,
                "has_firefly": 1 if name in ("流萤", "萨姆") else 0,
                "file": fpath.name, "title": f"{name}·{label}",
                "text": c, "trigger": "",
            })

    m = re.search(r"\|(?:角色)?介绍\s*=\s*(.+?)(?=\n\||\n\}\}|\Z)", raw, re.DOTALL)
    if m:
        _add("介绍", m.group(1))
    for i in range(1, 5):
        m = re.search(r"\|(?:角色)?故事" + str(i) + r"\s*=\s*(.+?)(?=\n\||\n\}\}|\Z)",
                      raw, re.DOTALL)
        if m:
            body = m.group(1).strip()
            if body and not body.startswith("{{"):
                _add(f"故事{i}", body)
    return chunks


def parse_voice(fpath: Path) -> list[dict]:
    """角色语音：每条中文语音一块。"""
    raw = fpath.read_text(encoding="utf-8", errors="ignore")
    name = fpath.stem.replace("_语音", "")
    blocks = re.findall(
        r"\|语音类型\s*=\s*(.+?)\s*\n.*?\|语音内容\s*=\s*(.+?)(?=\n\||\n\}\})",
        raw, re.DOTALL,
    )
    out: list[dict] = []
    for vtype, vcontent in blocks:
        vtype = vtype.strip()
        if VOICE_SKIP.search(vtype):
            continue
        vcontent = clean_wikitext(vcontent.strip())
        if len(vcontent) < 20:
            continue
        out.append({
            "source": "wiki", "category": "character_voice", "priority": 1,
            "has_firefly": 1 if name in ("流萤", "萨姆") else 0,
            "file": fpath.name, "title": f"{name}·语音·{vtype}",
            "text": vcontent, "trigger": "",
        })
    return out


_AEON_KEYWORDS = re.compile(r"阿哈|克里珀|阿基维利|博识尊|药师|岚|纳努克|伊德莉拉|浮黎|希佩|终末|虚无|繁育|智识|存护|巡猎|丰饶|毁灭|同谐|星神")
_FACTION_KEYWORDS = re.compile(r"流光忆庭|焚化工|假面愚者|博识学会|星际和平公司|自灭者|纯美骑士团|饮月君|云上五骁")


def parse_story(fpath: Path, category: str, priority: int) -> list[dict]:
    """任务/NPC 文件：剧情梗概单独成块 + 正文段落切块。"""
    raw = fpath.read_text(encoding="utf-8", errors="ignore")
    ff = 1 if has_firefly_marker(raw, fpath.name) else 0
    title = fpath.stem
    chunks: list[dict] = []

    m = re.search(r"\|\s*剧情梗概\s*=\s*(.+?)(?=\n\||\n\}\}|\Z)", raw, re.DOTALL)
    if m:
        summary = clean_wikitext(m.group(1))
        if len(summary) >= 30:
            chunks.append({
                "source": "wiki", "category": category, "priority": priority,
                "has_firefly": ff, "file": fpath.name,
                "title": f"{title}·剧情梗概",
                "text": summary[:CHUNK_MAX], "trigger": "",
            })

    body = clean_wikitext(raw)
    for c in chunk_paragraphs(body, title):
        chunks.append({
            "source": "wiki", "category": category, "priority": priority,
            "has_firefly": ff, "file": fpath.name, "title": title,
                "text": c, "trigger": "",
            })
    return chunks


# ══════════════════ 流萤官方原文 ══════════════════

# 游戏机制行 — 纯操作提示，对剧情问答是噪声
_OFFICIAL_GAMEPLAY_SKIP = re.compile(
    r"^(进入战斗|战斗结束|获得|解锁|任务完成|返回|存档|读取|"
    r"选项[一二三四]|选择\s*[：:]|线索|调查|探索|交互"
    r")"
)


def _story_sort_key(name: str) -> tuple:
    """主线剧情文件名按数字排序：'1. 无眠之夜' → (1, '无眠之夜')。"""
    m = re.match(r"^(\d+)\.\s*(.*)", name)
    if m:
        return (int(m.group(1)), m.group(2))
    return (99999, name)


def _parse_official_meta(text: str) -> dict[str, str]:
    """解析官方原文文件的元信息头（名称/来源/分类/人物/等级/时间线）。"""
    meta: dict[str, str] = {}
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("---"):
            break
        if m := re.match(r"^(.+?)[：:]\s*(.+)", line):
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key and val:
                meta[key] = val
    return meta


def _strip_official_header(text: str) -> str:
    """去掉官方原文的元信息头，取最后一个 --- 之后的内容。"""
    parts = text.split("\n---")
    return parts[-1].strip()


def _clean_dialogue_text(text: str) -> str:
    """清洗官方对话/剧本文本：去游戏机制行和 # 标题，保留对话与舞台指示。"""
    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        if _OFFICIAL_GAMEPLAY_SKIP.match(stripped):
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(">"):
            stripped = stripped[1:].strip()
        cleaned.append(stripped)
    # 合并连续空行
    result = "\n".join(cleaned)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _parse_official_main_story() -> list[dict]:
    """主线剧情文本：22 个 .md 文件，官方剧本/对话格式。category: story_main_official, priority: 1。"""
    root = FIREFLY_OFFICIAL_ROOT / "主线剧情文本"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(root.glob("*.md"), key=lambda p: _story_sort_key(p.stem)):
        text = f.read_text(encoding="utf-8")
        meta = _parse_official_meta(text)
        body = _strip_official_header(text)
        body = _clean_dialogue_text(body)
        title = meta.get("名称") or f.stem
        for c in chunk_paragraphs(body, title):
            if len(c) < 25:
                continue
            out.append({
                "source": "firefly_official", "category": "story_main_official",
                "priority": 1, "has_firefly": 1, "file": f.name,
                "title": f"主线·{title}", "text": f"【主线·{title}】{c}", "trigger": "",
            })
    return out


def _parse_official_character_stories() -> list[dict]:
    """角色游戏文本：角色故事 1-4（叙事散文）。category: character_story_official, priority: 1。"""
    root = FIREFLY_OFFICIAL_ROOT / "角色游戏文本"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(root.glob("角色故事*.md")):
        text = f.read_text(encoding="utf-8")
        meta = _parse_official_meta(text)
        body = _strip_official_header(text)
        body = _clean_dialogue_text(body)
        title = meta.get("名称") or f.stem
        for c in chunk_paragraphs(body, title):
            if len(c) < 25:
                continue
            out.append({
                "source": "firefly_official", "category": "character_story_official",
                "priority": 1, "has_firefly": 1, "file": f.name,
                "title": f"角色故事·{title}", "text": f"【流萤·角色故事】{c}", "trigger": "",
            })
    return out


def _parse_official_voice() -> list[dict]:
    """角色语音：按话题分块，每块一个独立条目。category: character_voice_official, priority: 1。"""
    fpath = FIREFLY_OFFICIAL_ROOT / "角色游戏文本" / "角色语音.md"
    if not fpath.exists():
        return []
    text = fpath.read_text(encoding="utf-8")
    body = _strip_official_header(text)
    # 按空行分割话题块
    blocks = [b.strip() for b in body.split("\n\n") if b.strip()]
    out: list[dict] = []
    for block in blocks:
        lines = block.split("\n", 1)
        topic = lines[0].rstrip("：:")
        content = lines[1].strip() if len(lines) > 1 else block
        if len(content) < 10:
            continue
        # 排除战斗/系统类语音
        if VOICE_SKIP.search(topic):
            continue
        out.append({
            "source": "firefly_official", "category": "character_voice_official",
            "priority": 1, "has_firefly": 1, "file": fpath.name,
            "title": f"流萤语音·{topic}",
            "text": f"【流萤语音·{topic}】{content}", "trigger": "",
        })
    return out


def _parse_official_sms() -> list[dict]:
    """角色短信：每条短信整块入库。category: story_sms_official, priority: 2。"""
    root = FIREFLY_OFFICIAL_ROOT / "角色游戏文本" / "短信"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(root.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        meta = _parse_official_meta(text)
        body = _strip_official_header(text).strip()
        if len(body) < 15:
            continue
        title = meta.get("名称") or f.stem
        out.append({
            "source": "firefly_official", "category": "story_sms_official",
            "priority": 2, "has_firefly": 1, "file": f.name,
            "title": f"短信·{title}",
            "text": f"【流萤短信·{title}】{body}", "trigger": "",
        })
    return out


def _parse_official_video_texts() -> list[dict]:
    """官方视频文本：PV 剧本/对话格式。category: story_video_official, priority: 2。"""
    root = FIREFLY_OFFICIAL_ROOT / "官方视频文本"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(root.rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        meta = _parse_official_meta(text)
        body = _strip_official_header(text)
        body = _clean_dialogue_text(body)
        title = meta.get("名称") or f.stem
        for c in chunk_paragraphs(body, title):
            if len(c) < 25:
                continue
            out.append({
                "source": "firefly_official", "category": "story_video_official",
                "priority": 2, "has_firefly": 1, "file": f.name,
                "title": f"视频·{title}", "text": f"【视频·{title}】{c}", "trigger": "",
            })
    return out


# ══════════════════ 构建 ══════════════════

def collect_chunks() -> list[dict]:
    all_chunks: list[dict] = []
    all_chunks += parse_firefly_lore()
    all_chunks += parse_curated_cards()
    print(f"  L0 信源: {len(all_chunks)} 块")
    n = len(all_chunks)
    all_chunks += parse_world_lore()
    print(f"  L1 world_lore: {len(all_chunks) - n} 块")

    # 流萤官方原文
    n = len(all_chunks)
    all_chunks += _parse_official_main_story()
    print(f"  流萤主线剧情: {len(all_chunks) - n} 块")
    n = len(all_chunks)
    all_chunks += _parse_official_character_stories()
    all_chunks += _parse_official_voice()
    print(f"  流萤角色文本/语音: {len(all_chunks) - n} 块")
    n = len(all_chunks)
    all_chunks += _parse_official_sms()
    print(f"  流萤短信: {len(all_chunks) - n} 块")
    n = len(all_chunks)
    all_chunks += _parse_official_video_texts()
    print(f"  流萤视频文本: {len(all_chunks) - n} 块")

    for dname, (cat, pri) in CATEGORY_MAP.items():
        dirpath = WIKI_ROOT / dname
        if not dirpath.is_dir():
            continue
        n0 = len(all_chunks)
        for f in sorted(dirpath.glob("*.txt")):
            try:
                if cat == "character":
                    all_chunks += parse_character(f)
                elif cat == "character_voice":
                    all_chunks += parse_voice(f)
                else:
                    all_chunks += parse_story(f, cat, pri)
            except Exception as e:
                print(f"  [warn] {f.name}: {e}")
        print(f"  {dname}: {len(all_chunks) - n0} 块")
    return all_chunks


FACTS_YAML = PROJECT_ROOT / "data" / "knowledge" / "facts.yaml"


def parse_facts() -> list[dict]:
    """解析 facts.yaml, 返回声明式事实列表。

    格式: entity / scene / source / fact / block_flag / keywords / verified_against
    """
    if not FACTS_YAML.exists():
        print("  [warn] facts.yaml 不存在，跳过事实表构建")
        return []
    if yaml is None:
        print("  [warn] PyYAML 未安装 (pip install pyyaml)，跳过事实表构建")
        return []
    try:
        with open(FACTS_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as e:
        print(f"  [warn] facts.yaml 解析失败: {e}")
        return []
    raw = data.get("facts", []) if isinstance(data, dict) else []
    facts: list[dict] = []
    for item in raw:
        entity = str(item.get("entity", "")).strip()
        fact = str(item.get("fact", "")).strip()
        if not entity or not fact:
            continue
        facts.append({
            "entity": entity,
            "scene": str(item.get("scene") or "").strip(),
            "source": str(item.get("source", "情报")).strip(),
            "fact": fact,
            "block_flag": str(item.get("block_flag") or "").strip(),
            "keywords": str(item.get("keywords") or "").strip(),
        })
    return facts


def source_manifest_hash() -> str:
    h = hashlib.sha256()
    files: list[Path] = []
    if LORE_MD.exists():
        files.append(LORE_MD)
    if CARDS_DIR.is_dir():
        files += sorted(CARDS_DIR.glob("*.md"))
    # world_lore
    files += sorted(WORLD_LORE_DIR.glob("*_lore.md"))
    # facts 表
    if FACTS_YAML.exists():
        files.append(FACTS_YAML)
    # 流萤官方原文
    for d in ["主线剧情文本", "角色游戏文本", "官方视频文本"]:
        dp = FIREFLY_OFFICIAL_ROOT / d
        if dp.is_dir():
            files += sorted(dp.rglob("*.md"))
    for dname in CATEGORY_MAP:
        d = WIKI_ROOT / dname
        if d.is_dir():
            files += sorted(d.glob("*.txt"))
    for f in files:
        st = f.stat()
        h.update(f"{f.name}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()


def build(no_vectors: bool = False, force: bool = False) -> None:
    manifest = source_manifest_hash()
    if DB_PATH.exists() and not force:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            row = conn.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
            has_vec = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] > 0
            conn.close()
            if row and row[0] == manifest and (no_vectors or has_vec):
                print("源文件未变化，索引已是最新（--force 可强制重建）")
                return
        except Exception:
            pass

    print("收集并切块……")
    chunks = collect_chunks()
    print(f"共 {len(chunks)} 块")

    tmp = DB_PATH.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(str(tmp))
    conn.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE chunks(
            id INTEGER PRIMARY KEY,
            source TEXT, category TEXT, priority INTEGER,
            has_firefly INTEGER, file TEXT, title TEXT,
            text TEXT, trigger TEXT
        );
        CREATE VIRTUAL TABLE chunks_fts USING fts5(seg, content='', tokenize='unicode61');
        CREATE TABLE embeddings(chunk_id INTEGER PRIMARY KEY, vec BLOB);
    """)
    for i, c in enumerate(chunks, start=1):
        conn.execute(
            "INSERT INTO chunks(id, source, category, priority, has_firefly, file, title, text, trigger) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (i, c["source"], c["category"], c["priority"], c["has_firefly"],
             c["file"], c["title"], c["text"], c["trigger"]),
        )
        conn.execute("INSERT INTO chunks_fts(rowid, seg) VALUES(?,?)",
                     (i, segment_for_fts(c["title"] + " " + c["text"])))
    conn.commit()
    print("FTS5 索引完成")

    # ── Facts 表构建 ──
    facts = parse_facts()
    if facts:
        conn.executescript("""
            CREATE TABLE facts(
                id INTEGER PRIMARY KEY,
                entity TEXT NOT NULL,
                scene TEXT,
                source TEXT NOT NULL,
                fact TEXT NOT NULL,
                block_flag TEXT,
                keywords TEXT
            );
            CREATE TABLE facts_kw(
                chunk_id INTEGER,
                keyword TEXT,
                PRIMARY KEY(chunk_id, keyword)
            ) WITHOUT ROWID;
        """)
        for i, f in enumerate(facts, start=1):
            conn.execute(
                "INSERT INTO facts(id, entity, scene, source, fact, block_flag, keywords) "
                "VALUES(?,?,?,?,?,?,?)",
                (i, f["entity"], f["scene"], f["source"], f["fact"],
                 f["block_flag"], f["keywords"]),
            )
            for kw in f["keywords"].split():
                kw = kw.strip()
                if kw:
                    conn.execute(
                        "INSERT OR IGNORE INTO facts_kw(chunk_id, keyword) VALUES(?,?)",
                        (i, kw),
                    )
        conn.commit()
        print(f"Facts 表: {len(facts)} 条事实")
    else:
        print("Facts 表: 无数据")

    if not no_vectors:
        print("加载 ONNX 模型……")
        from app.core.memory.embedding import OnnxEmbeddingEngine
        import numpy as np
        engine = OnnxEmbeddingEngine()
        engine._ensure_loaded()
        t0 = time.time()
        for i, c in enumerate(chunks, start=1):
            src = c.get("embed") or c["text"][:480]
            vec = np.asarray(engine.embed_text(src), dtype=np.float32)
            conn.execute("INSERT INTO embeddings(chunk_id, vec) VALUES(?,?)",
                         (i, vec.tobytes()))
            if i % 500 == 0:
                rate = i / (time.time() - t0)
                eta = (len(chunks) - i) / max(rate, 1e-6)
                print(f"  向量 {i}/{len(chunks)}  {rate:.0f}/s  ETA {eta/60:.1f}min")
                conn.commit()
        conn.commit()
        print(f"向量编码完成，耗时 {(time.time()-t0)/60:.1f} min")

    conn.execute("INSERT INTO meta(key, value) VALUES('manifest', ?)", (manifest,))
    conn.execute("INSERT INTO meta(key, value) VALUES('built_at', ?)",
                 (time.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.execute("INSERT INTO meta(key, value) VALUES('chunk_count', ?)", (str(len(chunks)),))
    conn.commit()
    conn.close()

    if DB_PATH.exists():
        try:
            DB_PATH.unlink()
            tmp.rename(DB_PATH)
        except Exception:
            import shutil
            shutil.copyfile(tmp, DB_PATH)
            tmp.unlink(missing_ok=True)
    else:
        tmp.rename(DB_PATH)
    print(f"索引已写入 {DB_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-vectors", action="store_true", help="跳过向量编码，只建 FTS")
    ap.add_argument("--force", action="store_true", help="忽略 hash 强制重建")
    args = ap.parse_args()
    build(no_vectors=args.no_vectors, force=args.force)
