"""游戏角色 / 设定 / 剧情「基于官方资料」后端检索注入。

当对话涉及《崩坏：星穹铁道》的游戏角色、设定、剧情时，后端主动检索
官方/权威资料（萌娘百科优先，维基百科兜底），将清洗后的资料注入
system prompt，使模型基于官方资料作答、不编造、不假设。

设计要点：
- 后端主动检索，模型本身没有「搜索 / 联网 / 工具」概念，天然不提获取过程。
- 与 `firefly_lore.md`（流萤第一人称主观记忆）分工：lore 覆盖的不重复查，
  官方资料只补 lore 未覆盖的客观设定。
- 仅当消息是「问游戏客观设定/事实」才触发；角色闲聊 / 主观偏好不触发。
"""
import asyncio
import logging
import re
import urllib.parse

logger = logging.getLogger(__name__)

# 仅允许抓取的权威域名白名单（防实体拼错抓到外部页面）
_ALLOWED_DOMAINS = ("zh.moegirl.org.cn", "bbs.mihoyo.com")

# —— 意图识别 ——
# 角色主观闲聊 / 偏好类（应走人设，不触发官方检索）
_CASUAL_SELF_WORDS = (
    "你喜欢", "你觉得", "你最", "你欣赏", "你怎看", "你认为", "你眼里",
    "你眼中的", "你对我", "你希望", "你在乎",
)
# 事实查询触发词（含其一即视为「问设定 / 事实」）
_LORE_QUESTION_WORDS = (
    "谁", "知道", "是什么", "设定", "剧情", "结局", "背景", "身份", "来自", "关系",
    "做了什么", "干了什么", "做了啥", "干啥", "干嘛", "干过什么",
    "怎么", "为什么", "哪", "多少", "技能", "命途", "星神",
    "介绍", "说说", "讲讲", "讲一下", "资料", "官网", "设定集", "起源",
    "死因", "真名", "阵营", "配音", "原型",
)

# —— 游戏实体词表（别名 → 规范词条名）——
# 注意：排除「流萤 / 萨姆」——那是伴侣本人，由人设 / lore 覆盖，不查官方。
_ENTITY_ALIASES: dict[str, str] = {
    # 角色
    "银狼": "银狼", "卡芙卡": "卡芙卡", "刃": "刃", "星期日": "星期日",
    "知更鸟": "知更鸟", "花火": "花火", "黄泉": "黄泉", "翡翠": "翡翠",
    "加拉赫": "加拉赫", "大丽花": "大丽花", "黑天鹅": "黑天鹅",
    "翁法罗斯": "翁法罗斯", "开拓者": "开拓者", "三月七": "三月七",
    "丹恒": "丹恒", "姬子": "姬子", "瓦尔特": "瓦尔特",
    "布洛妮娅": "布洛妮娅", "希儿": "希儿", "符玄": "符玄", "景元": "景元",
    "镜流": "镜流", "丹枢": "丹枢", "白露": "白露", "杰帕德": "杰帕德",
    "克拉拉": "克拉拉", "娜塔莎": "娜塔莎", "彦卿": "彦卿", "刃": "刃",
    "罗刹": "罗刹", "刃": "刃", "托帕": "托帕", "砂金": "砂金",
    "黄泉": "黄泉", "云璃": "云璃", "飞霄": "飞霄", "椒丘": "椒丘",
    "貊泽": "貊泽", "知更鸟": "知更鸟", "乱破": "乱破", "渔阳": "渔阳",
    "艾利欧": "艾利欧", "卡芙卡": "卡芙卡", "螺丝咕姆": "螺丝咕姆",
    "阮·梅": "阮·梅", "黑天鹅": "黑天鹅", "真理医生": "真理医生", "白厄": "白厄",
    "米莎": "米莎", "米哈伊尔": "钟表匠", "钟表匠": "钟表匠",
    # 翁法罗斯篇角色
    "阿格莱雅": "阿格莱雅", "万敌": "万敌", "遐蝶": "遐蝶", "那刻夏": "那刻夏",
    "风堇": "风堇", "赛飞儿": "赛飞儿", "海瑟音": "海瑟音", "刻律德菈": "刻律德菈",
    "昔涟": "昔涟", "长夜月": "长夜月",
    # 设定 / 地点 / 概念
    "匹诺康尼": "匹诺康尼", "翁法罗斯": "翁法罗斯", "星穹列车": "星穹列车",
    "星核猎手": "星核猎手", "格拉默": "格拉默", "命途": "命途",
    "星神": "星神", "巡猎": "巡猎", "丰饶": "丰饶", "毁灭": "毁灭",
    "同谐": "同谐", "终末": "终末", "虚无": "虚无", "繁育": "繁育",
    "智识": "智识", "存护": "存护", "记忆命途": "记忆命途",
    "流光忆庭": "流光忆庭", "联觉梦境": "联觉梦境", "家族": "家族",
    "谐乐大典": "谐乐大典", "热砂盛典": "热砂盛典", "仙舟": "仙舟",
    "持明": "持明", "云上五骁": "云上五骁", "焚化工": "焚化工",
    "何物朝向死亡": "何物朝向死亡", "忆域迷因": "忆域迷因",
    "星核": "星核", "星穹": "星穹", "虚数": "虚数", "裂界": "裂界",
    "智库": "智库", "黑塔": "黑塔", "博识学会": "博识学会",
    "公司": "星际和平公司", "丰饶民": "丰饶民", "巡猎者": "巡猎者",
}

# 中 → 英译名表（用于英文维基百科检索；英文标题多为角色英文名）
_ENTITY_EN: dict[str, str] = {
    "花火": "Firefly", "知更鸟": "Robin", "星期日": "Sunday", "黄泉": "Acheron",
    "翡翠": "Jade", "加拉赫": "Gallagher", "黑天鹅": "Black Swan", "银狼": "Silver Wolf",
    "卡芙卡": "Kafka", "刃": "Blade", "镜流": "Jingliu", "景元": "Jing Yuan",
    "符玄": "Fu Xuan", "希儿": "Seele", "布洛妮娅": "Bronya", "丹恒": "Dan Heng",
    "三月七": "March 7th", "开拓者": "Trailblazer", "姬子": "Himeko", "瓦尔特": "Welt",
    "罗刹": "Luocha", "托帕": "Topaz", "砂金": "Aventurine", "云璃": "Yunli",
    "飞霄": "Feixiao", "椒丘": "Jiaoqiu", "真理医生": "Dr. Ratio", "阮·梅": "Ruan Mei",
    "螺丝咕姆": "Screwllum", "艾利欧": "Elio", "大丽花": "Dahlia", "乱破": "Rappa", "白厄": "Phainon",
    "阿格莱雅": "Aglaea", "万敌": "Mydei", "遐蝶": "Castorice", "那刻夏": "Anaxa",
    "风堇": "Hyacine", "赛飞儿": "Cipher", "海瑟音": "Hysilens", "刻律德菈": "Cerydra",
    "昔涟": "Cyrene", "长夜月": "Evernight",
    "匹诺康尼": "Penacony", "翁法罗斯": "Amphoreus", "星穹列车": "Astral Express",
    "星核猎手": "Stellaron Hunters", "格拉默": "Glamoth", "命途": "Path",
    "星神": "Aeon", "巡猎": "The Hunt", "丰饶": "Abundance", "毁灭": "Destruction",
    "同谐": "Harmony", "终末": "Finality", "虚无": "Nihility", "智识": "Erudition",
    "存护": "Preservation", "仙舟": "Xianzhou", "持明": "Vidyadhara",
    "流光忆庭": "Memory's Legacies", "联觉梦境": "Dreamscape",
}


def _detect_entities(text: str) -> list[str]:
    found: list[str] = []
    for alias, canonical in _ENTITY_ALIASES.items():
        if alias in text and canonical not in found:
            found.append(canonical)
    return found


# 动态实体抽取：形如「你知道X吗 / X是谁 / 讲讲X」的提问，X 视为待查实体。
# 用于覆盖实体词表遗漏的角色名，降低模型凭空编造的概率。
_QENTITY_PATTERNS = [
    r"你知道(.+?)吗", r"你认识(.+?)吗", r"了解(.+?)吗", r"讲讲(.+?)吗", r"说说(.+?)吗",
    r"(.+?)是谁", r"(.+?)是啥",
    r"关于(.+?)的",
    r"讲讲(.+)$", r"说说(.+)$", r"介绍[一]?下(.+)$", r"讲一下(.+)$",
]
# 抽取名含人称代词时基本不是游戏角色名，直接排除
_QENTITY_PRONOUNS = ("我", "你", "他", "她", "它", "这", "那", "我们", "你们", "他们")


def _extract_question_entity(text: str) -> str:
    """从「你知道X吗 / X是谁」类提问中抽取待查实体名（角色/设定）。"""
    for pat in _QENTITY_PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        name = m.group(1).strip()
        name = re.sub(r"的.*$", "", name).strip()  # 去掉「的…」后缀
        if 1 < len(name) <= 8 and not re.search(r"[，。？?！!、,.\s]", name) \
                and not any(p in name for p in _QENTITY_PRONOUNS):
            return name
    return ""


def _looks_like_entity_question(text: str) -> bool:
    return bool(_extract_question_entity(text))


def involves_game_lore(text: str) -> bool:
    """判断消息是否涉及游戏角色 / 设定 / 剧情的客观事实查询。"""
    if not text:
        return False
    # 角色主观闲聊 / 偏好 → 不触发
    if any(w in text for w in _CASUAL_SELF_WORDS):
        return False
    # 必须命中游戏实体
    if not _detect_entities(text):
        # 兜底：形如「你知道X吗 / X是谁」的提问，X 视为待查实体，
        # 降低实体表遗漏导致模型凭空编造的问题
        return _looks_like_entity_question(text)
    # 须是事实查询（问号或疑问词）
    if "？" in text or "?" in text:
        return True
    if any(w in text for w in _LORE_QUESTION_WORDS):
        return True
    return False


def extract_game_entities(text: str, limit: int = 2) -> list[str]:
    """提取要查询的游戏实体（角色 / 设定名），最多 limit 个。"""
    return _detect_entities(text)[:limit]


# —— 缓存（实体 → 清洗后正文）——
_CACHE: dict[str, str] = {}
_CACHE_MAX = 200


def _cache_get(entity: str):
    return _CACHE.get(entity)


def _cache_set(entity: str, text: str) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        # 简单淘汰：清空一半
        for k in list(_CACHE)[: _CACHE_MAX // 2]:
            _CACHE.pop(k, None)
    _CACHE[entity] = text


def _fetch_moegirl_text(entity: str) -> str:
    """抓取萌娘百科词条正文并清洗（≤800 字）。失败返回空串。"""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("[游戏设定] requests/bs4 未安装，无法抓取萌娘百科")
        return ""

    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

        # 1) 通过 Special:Search 定位最相关词条
        #    （moegirl 的 api.php 需鉴权被限制，改用 HTML 搜索页）
        title_href: str | None = None
        try:
            sr = session.get(
                "https://zh.moegirl.org.cn/index.php",
                params={
                    "search": f"{entity} 崩坏星穹铁道",
                    "title": "Special:Search",
                    "fulltext": "1",
                },
                timeout=12,
            )
            if sr.status_code == 200:
                soup = BeautifulSoup(sr.text, "html.parser")
                results = soup.select(".mw-search-result-heading a")
                # 评分：含实体名(+2)，属于星穹铁道/崩坏(+1)，取最高分结果
                # 注意：搜索结果标题 <a> 文本为空，标题在 URL 编码的 href 中
                best = None
                best_score = -1
                for a in results:
                    href = a.get("href", "")
                    title = urllib.parse.unquote(href).split("#")[0].lstrip("/")
                    score = 0
                    if entity in title:
                        score += 2
                    if "星穹铁道" in title or "崩坏" in title:
                        score += 1
                    if score > best_score:
                        best_score = score
                        best = href
                title_href = best
        except Exception as e:
            logger.warning("[游戏设定] 搜索页失败 %s: %s", entity, e)

        # 2) 抓词条正文
        if title_href:
            page_url = "https://zh.moegirl.org.cn" + title_href
        else:
            page_url = "https://zh.moegirl.org.cn/" + urllib.parse.quote(entity)
        r2 = session.get(page_url, timeout=12)
        if r2.status_code != 200:
            return ""
        r2.encoding = "utf-8"
        soup = BeautifulSoup(r2.text, "html.parser")
        content = soup.select_one("#mw-content-text") or soup
        # 去除信息框 / 导航 / 模板噪音
        for tag in content.select(
            ".infobox, .navbox, .toc, .reference, .mw-editsection, "
            "table, .thumb, .notice, .box, style, script, .mw-empty-elt"
        ):
            tag.decompose()
        paragraphs = [p.get_text(separator="", strip=True) for p in content.select("p")]
        # 兜底：段落过少时补充列表/定义项
        if len([p for p in paragraphs if len(p) > 4]) < 2:
            paragraphs += [li.get_text(separator="", strip=True) for li in content.select("li, dd")]
        text = "\n".join(p for p in paragraphs if len(p) > 2)
        if not text:
            return ""
        if len(text) > 800:
            text = text[:800] + "…"
        return text.strip()
    except Exception as e:
        logger.warning("[游戏设定] 抓取萌娘百科失败 %s: %s", entity, e)
        return ""


def _fetch_wikipedia_text(entity: str) -> str:
    """从维基百科（中文优先，英文兜底）抓取权威正文（≤800 字）。

    作为萌娘百科被反爬拦截时的可达兜底源；维基百科 API 通常不挑战程序请求。
    返回清洗后的明文，失败返回空串。
    """
    try:
        import requests
    except ImportError:
        return ""

    en_name = _ENTITY_EN.get(entity, entity)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    # (语言, 检索词, 标题需包含的名称)
    queries = [
        ("zh", f"{entity} 崩坏星穹铁道 星穹铁道", entity),
        ("en", f"{en_name} Honkai Star Rail", en_name),
    ]
    for lang, query, name in queries:
        try:
            api = f"https://{lang}.wikipedia.org/w/api.php"
            # 1) 搜索定位词条
            sr = requests.get(
                api,
                params={
                    "action": "query", "list": "search",
                    "srsearch": query, "srlimit": 4, "format": "json",
                },
                timeout=10, headers=headers,
            )
            if sr.status_code != 200:
                continue
            results = sr.json().get("query", {}).get("search", [])
            best = None
            best_score = -1
            for it in results:
                t = it.get("title", "")
                score = 0
                if name and name.lower() in t.lower():
                    score += 2
                if "星穹铁道" in t or "崩坏" in t or "Star Rail" in t or "Honkai" in t:
                    score += 1
                if score > best_score:
                    best_score = score
                    best = t
            # 仅当标题含角色/设定名时采用，避免误用游戏主页等泛化页面
            if not best or best_score < 2:
                continue
            # 2) 取正文摘要
            ex = requests.get(
                api,
                params={
                    "action": "query", "prop": "extracts",
                    "explaintext": 1, "exchars": 1500,
                    "titles": best, "format": "json",
                },
                timeout=10, headers=headers,
            )
            if ex.status_code != 200:
                continue
            pages = ex.json().get("query", {}).get("pages", {})
            for _pid, pg in pages.items():
                txt = (pg.get("extract") or "").strip()
                if not txt:
                    continue
                # 去维基章节标记与粗体符号
                txt = re.sub(r"==+.*?==+", " ", txt)
                txt = txt.replace("'''", "")
                if len(txt) > 800:
                    txt = txt[:800] + "…"
                return txt
        except Exception as e:
            logger.warning("[游戏设定] 维基百科(%s)失败 %s: %s", lang, entity, e)
            continue
    return ""


def _fetch_official(entity: str) -> str:
    """按优先级抓取官方/权威资料：萌娘百科 → 维基百科。"""
    text = _fetch_moegirl_text(entity)
    if text:
        return text
    return _fetch_wikipedia_text(entity)


async def build_game_lore_context(user_message: str) -> str:
    """返回应注入 system prompt 的官方资料段；无需检索时返回空串。

    失败时也返回一段「勿臆测」约束，防止模型退回凭记忆编造。
    """
    if not involves_game_lore(user_message):
        return ""

    entities = extract_game_entities(user_message, limit=2)
    dynamic = False
    if not entities:
        # 兜底：从「你知道X吗 / X是谁」类提问中抽取角色名，覆盖实体词表遗漏的项
        dyn = _extract_question_entity(user_message)
        if dyn:
            entities = [dyn]
            dynamic = True
        else:
            return ""

    snippets: list[str] = []
    for ent in entities:
        text = _cache_get(ent)
        if text is None:
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_official, ent), timeout=12
                )
            except (asyncio.TimeoutError, Exception):
                text = ""
            _cache_set(ent, text)
        if text:
            snippets.append(f"### {ent}\n{text}")

    if not snippets:
        if dynamic:
            # 动态抽取的名字未检索到官方资料，且不确定是否为游戏内容，
            # 不注入「勿臆测」约束，避免干扰非游戏提问，交由模型正常回答。
            return ""
        # 抓取失败 / 为空 → 注入「不编造」约束
        return (
            "\n\n## 官方资料参考\n"
            "本次未能获取到关于该游戏设定的官方资料。"
            "若被问及游戏角色 / 设定 / 剧情的具体事实，请勿凭记忆臆测或补充不确定的内容，"
            '可温柔地说「这方面官方没有明确资料呢，要不我们去官网看看？」。'
        )

    joined = "\n\n".join(snippets)
    return (
        "\n\n## 官方资料参考（来自官方 / 权威来源，是你作答游戏设定的事实依据）\n"
        f"{joined}\n\n"
        "作答要求：\n"
        "- 仅基于以上资料回答游戏角色 / 设定 / 剧情的事实问题；资料中未提及的内容，"
        '坦率说「这方面官方没有明确资料呢」，绝不编造或假设。\n'
        "- 以你（流萤）的口吻自然转述这些事实，不以百科 / 参考资料口吻复述，"
        "且不要提及你查阅、搜索或联网等获取过程。"
    )
