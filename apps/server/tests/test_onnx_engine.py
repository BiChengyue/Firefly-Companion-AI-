"""Phase 14 验收测试 — ONNX 真语义引擎。

验证: 模型加载、embed_text 输出维度、L2 归一化、跨语言相似度。
标记 onnx 的原因：首次运行需下载 ~120MB 模型，CI 环境可能不可用。
"""
import numpy as np
import pytest

from app.core.memory.embedding import (
    OnnxEmbeddingEngine,
    LocalEmbeddingEngine,
    get_embedding_engine,
    create_embedding_engine,
    cosine_similarity,
)


class TestOnnxEmbeddingEngine:
    """ONNX 引擎核心功能测试（标记 onnx，需确保模型已下载）。"""

    @pytest.mark.onnx
    def test_engine_creates_and_loads(self):
        engine = OnnxEmbeddingEngine()
        assert engine.DIMENSION == 384
        # 延迟加载，首次 embed_text 触发
        vec = engine.embed_text("Hello world")
        assert vec.shape == (384,)
        assert vec.dtype == np.float32
        # L2 归一化验证
        norm = float(np.linalg.norm(vec))
        assert abs(norm - 1.0) < 1e-4

    @pytest.mark.onnx
    def test_empty_text_returns_zero_vector(self):
        engine = OnnxEmbeddingEngine()
        vec = engine.embed_text("")
        assert vec.shape == (384,)
        assert np.allclose(vec, 0.0)
        vec2 = engine.embed_text("   ")
        assert np.allclose(vec2, 0.0)

    @pytest.mark.onnx
    def test_semantic_similarity_chinese(self):
        engine = OnnxEmbeddingEngine()
        # "MacBook" 和 "笔记本电脑" 应该有一定语义相似度（非 0）
        v1 = engine.embed_text("MacBook")
        v2 = engine.embed_text("笔记本电脑")
        sim = cosine_similarity(v1, v2)
        assert sim > 0.30, f"Expected sim > 0.30, got {sim:.4f}"

    @pytest.mark.onnx
    def test_cross_language_similarity(self):
        engine = OnnxEmbeddingEngine()
        # "鼠标" 和 "mouse" 跨语言应有语义关联
        v1 = engine.embed_text("鼠标")
        v2 = engine.embed_text("mouse")
        sim = cosine_similarity(v1, v2)
        assert sim > 0.30, f"Expected cross-language sim > 0.30, got {sim:.4f}"

    @pytest.mark.onnx
    def test_hometown_semantic_match(self):
        """验证 ONNX 引擎能建立「家乡在哪」↔「是杭州人」的语义关联。"""
        engine = OnnxEmbeddingEngine()
        v1 = engine.embed_text("你还记得我的家乡在哪吗")
        v2 = engine.embed_text("用户是杭州人")
        sim = cosine_similarity(v1, v2)
        assert sim > 0.30, f"Expected semantic sim > 0.30, got {sim:.4f}"

    @pytest.mark.onnx
    def test_same_meaning_similarity_high(self):
        """同一含义的不同表述应有高相似度。"""
        engine = OnnxEmbeddingEngine()
        v1 = engine.embed_text("我喜欢喝咖啡")
        v2 = engine.embed_text("咖啡是我的最爱")
        sim = cosine_similarity(v1, v2)
        assert sim > 0.50, f"Expected high sim > 0.50, got {sim:.4f}"

    @pytest.mark.onnx
    def test_unrelated_texts_low_similarity(self):
        """不相关内容应有低相似度。"""
        engine = OnnxEmbeddingEngine()
        v1 = engine.embed_text("今天天气真好")
        v2 = engine.embed_text("Python 是一门编程语言")
        sim = cosine_similarity(v1, v2)
        assert sim < 0.50, f"Expected sim < 0.50 for unrelated texts, got {sim:.4f}"


class TestLocalEmbeddingEngine:
    """哈希引擎回退测试（始终可用，无需 ONNX 依赖）。"""

    def test_output_dimension(self):
        engine = LocalEmbeddingEngine()
        vec = engine.embed_text("测试文本")
        assert vec.shape == (384,)
        assert vec.dtype == np.float32

    def test_l2_normalized(self):
        engine = LocalEmbeddingEngine()
        vec = engine.embed_text("Hello world 你好")
        norm = float(np.linalg.norm(vec))
        assert abs(norm - 1.0) < 1e-4

    def test_empty_text_zero(self):
        engine = LocalEmbeddingEngine()
        vec = engine.embed_text("")
        assert np.allclose(vec, 0.0)


class TestEngineFactory:
    """引擎工厂函数测试。"""

    def test_create_hash_engine(self):
        engine = create_embedding_engine(engine_type="hash")
        assert isinstance(engine, LocalEmbeddingEngine)

    def test_create_onnx_engine(self):
        engine = create_embedding_engine(engine_type="onnx")
        assert isinstance(engine, OnnxEmbeddingEngine)

    def test_get_embedding_engine_returns_instance(self):
        engine = get_embedding_engine()
        assert engine is not None
        assert hasattr(engine, "embed_text")
