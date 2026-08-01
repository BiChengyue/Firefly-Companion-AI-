"""HSRChat wiki 索引构建脚本。

遍历 resources/hsrchat/references/wiki/ 下 7 个有用分类：
  开拓任务 / 开拓续闻 / 同行任务 / 冒险任务 / 角色 / 角色语音 / NPC

按分类规则过滤、解析流萤出场标记、向量化后输出 data/hsr_index.json。

用法：
  cd apps/server && python scripts/build_hsr_index.py
"""
import base64
import json
import os
import re
import struct
import sys
import time
from pathlib import Path

import numpy as np

# 将 server 根目录加入 sys.path，方便 import app 模块
_SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_ROOT))

from app.core.memory.embedding import get_embedding_engine

# ── 路径配置 ──
# 从 scripts/ 向上走到项目根: apps/server/scripts → apps/server → apps → (project root)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_WIKI_BASE = _PROJECT_ROOT / "resources" / "hsrchat" / "references" / "wiki"
_OUTPUT_PATH = _PROJECT_ROOT / "data" / "hsr_index.json"

# ── 保留的分类 ──
_INCLUDED_CATEGORIES = {
    "开拓任务": "story_main",
    "开拓续闻": "story_side",
    "同行任务": "story_companion",
    "冒险任务": "story_adventure",
    "角色": "character",
    "角色语音": "character_voice",
    "NPC": "npc",
}

# ── MediaWiki 模板壳模式（跳过）──
_TEMPLATE_SHELL_PATTERN = re.compile(r"^\{\{(?:任务|系列任务|角色图鉴|NPC|角色语音)[\s\S]*?\}\}$", re.MULTILINE)


def _is_pure_template_shell(text: str) -> bool:
    """判断文件是否为无正文的纯 MediaWiki 模板壳。"""
    # 去除注释和空行
    cleaned = re.sub(r"<!--[\s\S]*?-->", "", text).strip()
    # 如果文本很短（<500字符），视为模板壳
    if len(cleaned) < 500:
        return True
    # 如果没有 ==剧情内容== 或 == 章节标记，且开头是 {{
    if cleaned.startswith("{{") and "==" not in cleaned:
        return True
    return False


def _extract_appears(text: str) -> list[str]:
    """从任务文件头部提取 |出场人物= 字段。"""
    m = re.search(r"\|出场人物\s*=\s*(.+?)(?=\n\||\n\})", text)
    if not m:
        return []
    chars = m.group(1).strip()
    # 支持顿号、逗号、中文逗号分隔
    return [c.strip() for c in re.split(r"[、,，]", chars) if c.strip()]


def _has_firefly_in_appears(text: str) -> bool:
    """判断出场人物是否含流萤/萨姆。"""
    appears = _extract_appears(text)
    return any("流萤" in a or "萨姆" in a for a in appears)


def _split_task_sections(text: str, source_file: str, source_category: str) -> list[dict]:
    """按 === 章节 === 切分任务/剧情文件为块。"""
    # 去除模板头
    body = re.sub(r"^\{\{[\s\S]*?\}\}", "", text, count=1).strip()

    # 按 === 分割
    parts = re.split(r"(={2,5}\s*.+?\s*={2,5})", body)
    chunks = []
    current_title = ""
    has_firefly = _has_firefly_in_appears(text)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.match(r"^={2,5}\s*.+?\s*={2,5}$", part):
            current_title = part.strip("=").strip()
        else:
            # 清理角色对话模板
            cleaned = re.sub(r"\{\{角色对话[^}]*?\}\}", "", part)
            cleaned = re.sub(r"<[^>]+>", "", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if len(cleaned) < 20:
                continue

            chunk = {
                "source_file": source_file,
                "source_category": source_category,
                "chunk_title": current_title or os.path.splitext(source_file)[0],
                "text": cleaned,
            }
            if has_firefly:
                chunk["character_present"] = "firefly"
            chunks.append(chunk)

    return chunks


def _extract_character_content(text: str, source_file: str, source_category: str) -> list[dict]:
    """从角色文件中提取 介绍 + 角色故事1-4。"""
    chunks = []

    # 提取介绍
    intro_m = re.search(r"\|介绍\s*=\s*(.+?)(?=\n\||\n\})", text)
    if intro_m:
        intro = intro_m.group(1).strip()
        if len(intro) > 10:
            chunks.append({
                "source_file": source_file,
                "source_category": source_category,
                "chunk_title": f"{os.path.splitext(source_file)[0]}·介绍",
                "text": intro,
            })

    # 提取角色故事 1-4
    for i in range(1, 5):
        story_m = re.search(rf"\|角色故事{i}\s*=\s*(.+?)(?=\n\||\n\}})", text)
        if story_m:
            story = story_m.group(1).strip()
            if len(story) > 20:
                chunk = {
                    "source_file": source_file,
                    "source_category": source_category,
                    "chunk_title": f"{os.path.splitext(source_file)[0]}·故事{i}",
                    "text": story,
                }
                # 流萤自身 → 标记
                if "流萤.txt" in source_file or "萨姆.txt" in source_file:
                    chunk["character_present"] = "firefly"
                chunks.append(chunk)

    return chunks


def _extract_voice_content(text: str, source_file: str, source_category: str) -> list[dict]:
    """从角色语音文件中提取中文语音内容。"""
    chunks = []
    # 匹配 |语音内容=中文文本（非日/英/韩）
    pattern = re.compile(
        r"\|语音内容\s*=\s*(.+?)(?=\n\||\n\})",
        re.DOTALL,
    )
    # 获取语音类型
    type_pattern = re.compile(r"\|语音类型\s*=\s*(.+?)(?=\n\||\n\})")

    entries = re.split(r"\{\{角色语音", text)
    for entry in entries:
        if not entry.strip():
            continue

        voice_type_m = type_pattern.search(entry)
        voice_type = voice_type_m.group(1).strip() if voice_type_m else ""

        content_m = pattern.search(entry)
        if not content_m:
            continue
        content = content_m.group(1).strip()
        if len(content) < 2:
            continue

        char_name = os.path.splitext(source_file)[0].replace("_语音", "")
        chunk = {
            "source_file": source_file,
            "source_category": source_category,
            "chunk_title": f"{char_name}·{voice_type or '语音'}",
            "text": content,
        }
        if "流萤" in source_file or "萨姆" in source_file:
            chunk["character_present"] = "firefly"
        chunks.append(chunk)

    return chunks


def _extract_npc_content(text: str, source_file: str, source_category: str) -> list[dict]:
    """处理 NPC 文件（同任务文件的章节切分逻辑）。"""
    return _split_task_sections(text, source_file, source_category)


# ── 分类处理路由 ──
_CATEGORY_PROCESSORS = {
    "开拓任务": _split_task_sections,
    "开拓续闻": _split_task_sections,
    "同行任务": _split_task_sections,
    "冒险任务": _split_task_sections,
    "角色": _extract_character_content,
    "角色语音": _extract_voice_content,
    "NPC": _extract_npc_content,
}


def build_index() -> list[dict]:
    """遍历 wiki 目录，构建所有文本块（含向量）。"""
    if not _WIKI_BASE.exists():
        print(f"[ERROR] wiki 目录不存在: {_WIKI_BASE}")
        sys.exit(1)

    engine = get_embedding_engine()
    engine_type = type(engine).__name__
    print(f"[INFO] 使用向量引擎: {engine_type}")

    all_chunks: list[dict] = []
    total_files = 0
    skipped_files = 0

    for cat_name, cat_key in sorted(_INCLUDED_CATEGORIES.items()):
        cat_dir = _WIKI_BASE / cat_name
        if not cat_dir.exists():
            print(f"[WARN] 分类目录不存在, 跳过: {cat_name}")
            continue

        files = sorted(cat_dir.glob("*.txt"))
        cat_chunks = 0
        cat_processed = 0
        cat_skipped = 0

        processor = _CATEGORY_PROCESSORS[cat_name]

        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[WARN] 读取失败 {fp.name}: {e}")
                cat_skipped += 1
                continue

            # 模板壳过滤（仅对任务类文件生效，角色/语音不跳过）
            if cat_name not in ("角色", "角色语音") and _is_pure_template_shell(text):
                cat_skipped += 1
                continue

            chunks = processor(text, fp.name, cat_key)
            cat_processed += 1

            for chunk in chunks:
                # 向量用 base64 编码 float32 二进制（比 JSON list 小 ~5x）
                vec = engine.embed_text(chunk["text"])
                chunk["vector"] = base64.b64encode(vec.astype(np.float32).tobytes()).decode("ascii")
                all_chunks.append(chunk)
                cat_chunks += 1

        print(f"  [{cat_name}] {cat_processed}个文件 → {cat_chunks}个块 (跳过{cat_skipped}个模板壳)")
        total_files += cat_processed
        skipped_files += cat_skipped

    print(f"\n[INFO] 总计: {total_files}个文件 → {len(all_chunks)}个块 (跳过{skipped_files}个模板壳)")
    return all_chunks


def main():
    start = time.time()

    print("=" * 60)
    print("HSRChat Wiki 索引构建")
    print(f"数据源: {_WIKI_BASE}")
    print(f"输出: {_OUTPUT_PATH}")
    print("=" * 60)

    chunks = build_index()

    if not chunks:
        print("[ERROR] 未生成任何索引块，请检查 wiki 数据源")
        sys.exit(1)

    # 统计
    firefly_chunks = sum(1 for c in chunks if c.get("character_present") == "firefly")
    by_cat = {}
    for c in chunks:
        cat = c["source_category"]
        by_cat[cat] = by_cat.get(cat, 0) + 1

    # 写入 JSON
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output_data = {
        "meta": {
            "version": 1,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_chunks": len(chunks),
            "firefly_chunks": firefly_chunks,
            "by_category": by_cat,
            "vector_dim": 384,  # 硬编码 384 维，由引擎保证
        },
        "chunks": chunks,
    }

    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False)

    # 报告
    file_size_mb = _OUTPUT_PATH.stat().st_size / (1024 * 1024)
    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"索引构建完成!")
    print(f"  总块数: {len(chunks)}")
    print(f"  流萤相关块: {firefly_chunks}")
    print(f"  分类分布: {json.dumps(by_cat, ensure_ascii=False)}")
    print(f"  文件大小: {file_size_mb:.1f} MB")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  输出: {_OUTPUT_PATH}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
