"""本地轻量级向量 Embedding 引擎 (Local Embedding Engine)。

专注于桌面端极速文本向量化、BLOB 序列化与 NumPy 余弦相似度计算。
与云端 LLM API 100% 解耦，零额外 Token 费用，零网络延迟。

支持双引擎：
- LocalEmbeddingEngine: 自研哈希投影引擎（384维，零模型文件，零外部依赖）
- OnnxEmbeddingEngine: ONNX 真语义引擎（paraphrase-multilingual-MiniLM-L12-v2，384维）
"""

import math
import os
import re
import struct
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 默认 ONNX 模型（HuggingFace 模型 ID）
DEFAULT_ONNX_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class LocalEmbeddingEngine:
    """自研哈希投影 Embedding 引擎（轻量回退方案）。

    基于 n-gram 伪随机哈希 + 23 类手工语义词库加权，384 维。
    零模型文件，零外部依赖，始终可用。"""

    DIMENSION: int = 384

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def embed_text(self, text: str) -> np.ndarray:
        """将文本映射为归一化的 float32 稠密向量。"""
        if not text or not text.strip():
            return np.zeros(self.dimension, dtype=np.float32)

        # 1. 文本预处理与特征 Token 化
        text = text.lower().strip()
        tokens = self._extract_semantic_features(text)

        # 2. 特征投影与向量构建
        vec = np.zeros(self.dimension, dtype=np.float64)
        for token in tokens:
            # 伪随机 hashing 杂凑投影，平滑分布
            h = hash(token)
            for i in range(4):  # 每个 token 激活 4 个维度的特征位
                idx = abs(hash(f"{token}_{i}")) % self.dimension
                val = 1.0 if (h & (1 << (i % 32))) else -1.0
                vec[idx] += val

        # 3. 同义词与范畴语义投影膨胀 (Semantic Expansion)
        self._apply_semantic_category_projections(text, vec)

        # 4. L2 归一化 (L2 Normalization)
        norm = np.linalg.norm(vec)
        if norm > 1e-9:
            vec = vec / norm
        else:
            vec = np.zeros(self.dimension, dtype=np.float64)

        return vec.astype(np.float32)

    def _extract_semantic_features(self, text: str) -> list[str]:
        """提取 1-gram, 2-gram 与英文单词概念。"""
        tokens: list[str] = []
        # 英文/数字词
        eng_words = re.findall(r"[a-z0-9]+", text)
        tokens.extend(eng_words)

        # 中文字符切分
        cjk_chunks = re.findall(r"[\u4e00-\u9fff]+", text)
        for chunk in cjk_chunks:
            for char in chunk:
                tokens.append(char)
            for i in range(len(chunk) - 1):
                tokens.append(chunk[i:i + 2])

        return tokens

    def _apply_semantic_category_projections(self, text: str, vec: np.ndarray):
        """对常见的隐式语义范畴进行向量加权映射（如操作系统、编程语言、兴趣等）。
        覆盖 23 类个人 AI 伴侣高频场景。"""
        categories = {
            # ── 开发工具（7 类原有） ──
            "os": ["操作系统", "mac", "windows", "linux", "macos", "ubuntu", "系统"],
            "backend": ["后端", "python", "fastapi", "django", "flask", "node", "golang", "java"],
            "frontend": ["前端", "vue", "react", "typescript", "ts", "javascript", "vite"],
            "database": ["数据库", "sqlite", "mysql", "postgresql", "chroma", "vector"],
            "editor": ["编辑器", "vscode", "cursor", "pycharm", "vim", "ide"],
            "language": ["编程语言", "代码", "语言", "开发", "写代码", "程序"],
            # ── 日常生活（新增 16 类） ──
            "music": ["音乐", "歌", "歌曲", "听歌", "网易云", "spotify", "qq音乐", "歌手", "乐队", "播放", "专辑"],
            "movie": ["电影", "视频", "剧", "追剧", "看剧", "b站", "bilibili", "动漫", "番剧", "纪录片"],
            "game": ["游戏", "steam", "switch", "ps5", "原神", "王者", "吃鸡", "竞技", "rpg", "打游戏"],
            "sport": ["运动", "跑步", "健身", "篮球", "足球", "羽毛球", "游泳", "瑜伽", "锻炼", "健身房"],
            "food": ["吃", "喝", "美食", "外卖", "做饭", "餐厅", "火锅", "奶茶", "咖啡", "茶", "饮料", "饭"],
            "sleep": ["睡眠", "睡觉", "熬夜", "早起", "失眠", "午睡", "作息", "起床", "闹钟"],
            "travel": ["旅行", "旅游", "出差", "景点", "酒店", "机票", "火车", "出行", "自驾"],
            "pet": ["宠物", "猫", "狗", "猫咪", "小狗", "养猫", "养狗", "铲屎", "喵", "汪"],
            "social": ["朋友", "同事", "同学", "聚会", "约会", "社交", "聊天", "微信"],
            "reading": ["书", "读书", "阅读", "小说", "文章", "kindle", "电子书", "漫画"],
            "shopping": ["买", "购物", "淘宝", "京东", "拼多多", "快递", "下单", "价格", "便宜"],
            "weather": ["天气", "下雨", "晴天", "冷", "热", "降温", "温度", "气候", "季节"],
            "holiday": ["生日", "节日", "春节", "圣诞", "放假", "假期", "过年"],
            "health": ["健康", "生病", "感冒", "医院", "药", "体检", "不舒服", "头疼"],
            "learning": ["学习", "学", "课程", "教程", "考试", "背单词", "刷题", "笔记"],
            "finance": ["钱", "工资", "理财", "股票", "基金", "投资", "存款", "预算"],
        }
        for cat_name, keywords in categories.items():
            if any(kw in text for kw in keywords):
                cat_hash = abs(hash(f"cat_{cat_name}")) % self.dimension
                vec[cat_hash] += 3.0


# ── ONNX 真语义引擎 ─────────────────────────────────────

class OnnxEmbeddingEngine:
    """ONNX 真语义 Embedding 引擎。

    基于 paraphrase-multilingual-MiniLM-L12-v2（384 维，中英双语）。
    首次加载会从 HuggingFace 下载并导出 ONNX 模型（~120MB），后续启动复用缓存。
    CPU 推理，桌面端友好。"""

    DIMENSION: int = 384
    MAX_LENGTH: int = 512

    def __init__(self, model_name_or_path: str = DEFAULT_ONNX_MODEL, cache_dir: Optional[str] = None):
        self._model_name = model_name_or_path
        self._cache_dir = cache_dir
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self):
        """加载 ONNX 模型（用 onnxruntime 原生 API，无需 optimum）。

        要求预先运行过 scripts/export_onnx.py 将模型导出为 ONNX 格式。
        """
        if self._model is not None:
            return

        logger.info(f"[ONNX] 加载模型: {self._model_name} …")

        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
            import os

            model_path = os.path.join(self._model_name, "model.onnx")
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"ONNX 模型文件不存在: {model_path}")

            self._session = ort.InferenceSession(
                model_path,
                providers=["CPUExecutionProvider"],
            )
            self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            self._model = True  # 标记已加载
            logger.info("[ONNX] 模型加载完成 (onnxruntime 原生模式)")
        except ImportError as e:
            raise RuntimeError(
                "ONNX 引擎需要安装额外依赖: pip install onnxruntime transformers sentencepiece"
            ) from e

    import functools

    @functools.lru_cache(maxsize=2048)
    def _calc_vector_cached(self, text: str) -> tuple[float, ...]:
        """内存缓存核心 ONNX 推理：常用短语/检索词 0ms 瞬间命中。"""
        self._ensure_loaded()

        inputs = self._tokenizer(
            text,
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=self.MAX_LENGTH,
        )

        # onnxruntime 原生推理：构建输入字典
        ort_inputs = {
            "input_ids": inputs["input_ids"],
            "attention_mask": inputs["attention_mask"],
        }
        # MiniLM exported ONNX may require token_type_ids
        if "token_type_ids" in inputs:
            ort_inputs["token_type_ids"] = inputs["token_type_ids"]
        else:
            ort_inputs["token_type_ids"] = np.zeros_like(inputs["input_ids"])

        ort_outputs = self._session.run(None, ort_inputs)
        token_embeddings = ort_outputs[0]  # last_hidden_state

        attention_mask = inputs["attention_mask"]
        mask_expanded = np.expand_dims(attention_mask, axis=-1)
        mask_expanded = np.broadcast_to(mask_expanded, token_embeddings.shape)
        sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
        sum_mask = np.clip(np.sum(attention_mask, axis=1, keepdims=True), a_min=1e-9, a_max=None)
        mean_embeddings = sum_embeddings / sum_mask

        vec = mean_embeddings[0].astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 1e-9:
            vec = vec / norm

        return tuple(vec.tolist())

    def embed_text(self, text: str) -> np.ndarray:
        """将文本映射为 ONNX 真语义归一化向量（384 维 float32，带 2048 高速率内存缓存）。"""
        clean = text.strip() if text else ""
        if not clean:
            return np.zeros(self.DIMENSION, dtype=np.float32)

        t_vec = self._calc_vector_cached(clean)
        return np.array(t_vec, dtype=np.float32)


# ── 向量序列化与数理计算原语 ───────────────────────────────

def vector_to_blob(vec: np.ndarray) -> bytes:
    """将 numpy float32 数组转换为二进制 BLOB 字节流。"""
    return vec.astype(np.float32).tobytes()


def blob_to_vector(blob: Optional[bytes], dimension: int = 384) -> Optional[np.ndarray]:
    """从 SQLite 二进制 BLOB 恢复 numpy float32 向量。"""
    if not blob:
        return None
    try:
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.shape[0] == dimension:
            return vec
        return None
    except Exception:
        return None


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """计算两个归一化向量的余弦相似度。"""
    if vec1 is None or vec2 is None:
        return 0.0
    dot = float(np.dot(vec1, vec2))
    norm1 = float(np.linalg.norm(vec1))
    norm2 = float(np.linalg.norm(vec2))
    if norm1 < 1e-9 or norm2 < 1e-9:
        return 0.0
    return dot / (norm1 * norm2)


# ── 引擎工厂与单例管理 ───────────────────────────────────

_engine_instance: Optional[LocalEmbeddingEngine | OnnxEmbeddingEngine] = None
_engine_type_loaded: Optional[str] = None


def create_embedding_engine(
    engine_type: str = "hash",
    onnx_model_path: str = DEFAULT_ONNX_MODEL,
    onnx_cache_dir: Optional[str] = None,
) -> LocalEmbeddingEngine | OnnxEmbeddingEngine:
    """工厂函数：按配置创建 Embedding 引擎实例。

    Args:
        engine_type: "hash" | "onnx"
        onnx_model_path: ONNX 模型 HuggingFace ID 或本地路径
        onnx_cache_dir: HuggingFace 缓存目录

    Returns:
        LocalEmbeddingEngine 或 OnnxEmbeddingEngine
    """
    if engine_type == "onnx":
        logger.info("[Embedding] 创建 ONNX 真语义引擎")
        return OnnxEmbeddingEngine(model_name_or_path=onnx_model_path, cache_dir=onnx_cache_dir)
    logger.info("[Embedding] 创建哈希投影引擎（回退模式）")
    return LocalEmbeddingEngine()


def get_embedding_engine() -> LocalEmbeddingEngine | OnnxEmbeddingEngine:
    """获取当前全局单例 Embedding 引擎（延迟初始化，按 config 选择引擎类型）。

    首次调用时从 Settings 读取 memory.embedding_engine 配置决定创建哪种引擎。
    若配置为 onnx 但模型未导出（无 model.onnx 缓存），自动回退 hash 引擎。
    后续调用返回同一实例。
    """
    global _engine_instance, _engine_type_loaded

    if _engine_instance is not None:
        return _engine_instance

    try:
        from app.config import get_settings
        settings = get_settings()
        engine_type = getattr(settings.memory, "embedding_engine", "hash")
        onnx_path = getattr(settings.memory, "onnx_model_path", DEFAULT_ONNX_MODEL)
        # 若配置的是相对路径（如 data/onnx_model），从项目根解析
        if onnx_path and not onnx_path.startswith(("sentence-transformers/", "/", "\\")) and ":" not in onnx_path:
            import os as _os
            _project_root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "..", "..", ".."))
            onnx_path = _os.path.join(_project_root, onnx_path)
    except Exception:
        engine_type = "hash"
        onnx_path = DEFAULT_ONNX_MODEL

    if engine_type == "onnx":
        try:
            engine = create_embedding_engine(
                engine_type="onnx",
                onnx_model_path=onnx_path,
            )
            engine._ensure_loaded()  # 启动时即验证 — export=False，零 spawn
            _engine_instance = engine
            _engine_type_loaded = "onnx"
            logger.info("[Embedding] ONNX 引擎就绪 ✓")
            return engine
        except Exception as e:
            logger.warning(
                "[Embedding] ONNX 引擎初始化失败（模型未导出？），"
                "回退 hash 引擎: %s", e
            )
            _engine_type_loaded = None  # 重置，允许后续切换回 ONNX

    # hash 引擎（始终可用）
    _engine_instance = create_embedding_engine(engine_type="hash")
    _engine_type_loaded = "hash"
    logger.info("[Embedding] 哈希引擎就绪 ✓")
    return _engine_instance


def reset_embedding_engine():
    """重置全局引擎单例（配置变更后调用，下次 get 时重新创建）。"""
    global _engine_instance, _engine_type_loaded
    _engine_instance = None
    _engine_type_loaded = None


# ── 双引擎混合召回：领域增强哈希引擎 ───────────────────────
_hash_engine: Optional[LocalEmbeddingEngine] = None


def get_hash_engine() -> LocalEmbeddingEngine:
    """获取独立哈希引擎实例（用于双引擎混合召回中的 23 类领域范畴增强）。

    与主引擎（ONNX / hash）并行运行，不写入 BLOB，仅在 recall() 时
    计算领域范畴投影相似度。ONNX 提供通用语义，哈希提供领域知识——
    如"奶茶"和"咖啡"同属 food 范畴，哈希能捕捉到这种关联。
    """
    global _hash_engine
    if _hash_engine is None:
        _hash_engine = LocalEmbeddingEngine()
    return _hash_engine


def reset_embedding_engine_all():
    """重置所有引擎实例（含哈希增强引擎）。"""
    global _engine_instance, _engine_type_loaded, _hash_engine
    _engine_instance = None
    _engine_type_loaded = None
    _hash_engine = None


# 兼容旧代码：模块级别名 → 延迟获取单例
local_embedding_engine = None  # 废弃，请使用 get_embedding_engine()
