"""记忆系统门面 (Memory Facade Manager)。

整合 PersonalMemoryManager（个人长期记忆）与 KnowledgeBaseManager（工作知识库）。
对外保持完全兼容的 API 接口与 `memory_manager` 全局单例。
"""

from typing import Optional
import time

from app.core import db as _db
from app.core.llm.base import LLMMessage
from app.core.memory.knowledge_base import KnowledgeBaseManager
from app.core.memory.personal import PersonalMemoryManager


class MemoryFacade:
    """记忆系统解耦门面管理器。"""

    def __init__(self):
        self.personal = PersonalMemoryManager()
        self.knowledge = KnowledgeBaseManager()

    @property
    def settings(self):
        return self.personal.settings

    # ── 短期缓冲包装（个人记忆） ─────────────────────────────

    def add_message(self, role: str, content: str, counting_for_extract: bool = True):
        self.personal.add_message(role, content, counting_for_extract=counting_for_extract)

    def get_short_term(self) -> list[dict]:
        return self.personal.get_short_term()

    def get_short_term_as_llm(self) -> list[LLMMessage]:
        return self.personal.get_short_term_as_llm()

    def clear_short_term(self):
        self.personal.clear_short_term()

    # ── 命名空间包装 ─────────────────────────────────────────

    def get_namespaces(self, mode: str) -> list[str]:
        return self.personal.get_namespaces(mode)

    def switch_namespace(self, new_mode: str):
        self.personal.switch_namespace(new_mode)

    # ── 个人长期记忆包装 ─────────────────────────────────────

    async def recall(
        self,
        query: str,
        mode: str = "daily",
        top_k: int = 5,
        min_similarity: float = 0.10,
    ) -> list[dict]:
        return await self.personal.recall(
            query=query,
            mode=mode,
            top_k=top_k,
            min_similarity=min_similarity,
        )

    async def write_long_term(
        self,
        content: str,
        metadata: dict,
        confidence: float,
        namespace: str = "shared_profile",
    ) -> bool:
        return await self.personal.write_long_term(
            content=content,
            metadata=metadata,
            confidence=confidence,
            namespace=namespace,
        )

    async def save_memory(
        self,
        mem_type: str,
        content: str,
        mode: str = "daily",
        confidence: float = 0.0,
    ) -> bool:
        """兼容旧调用签名（T-30 修复：chat.py promise 提醒写入）。

        结构化记忆写入：mode → namespace（daily→daily_life、work→work_tasks、其它→shared_profile），
        走 write_long_term（置信度门槛 + lore 泄漏拦截 + embedding 落库）。
        历史调用 `save_memory(type, content, mode, confidence)` 此前因本方法缺失抛
        AttributeError 被调用方吞掉（promise 从未落库）——补齐后桌宠待办与日报待办板块可用。
        """
        ns = "daily_life" if mode == "daily" else ("work_tasks" if mode == "work" else "shared_profile")
        return await self.personal.write_long_term(
            content=content,
            metadata={"type": mem_type},
            confidence=confidence,
            namespace=ns,
        )

    @staticmethod
    def _format_memories_for_prompt(recalled: list[dict]) -> str:
        """Phase 13: 将召回的记忆列表格式化为含时效标记的 LLM 注入文本。"""
        return PersonalMemoryManager._format_memories_for_prompt(recalled)

    async def extract_memories(
        self,
        provider,
        recent_messages: list[LLMMessage],
        mode: str = "daily",
    ) -> int:
        return await self.personal.extract_memories(
            provider=provider,
            recent_messages=recent_messages,
            mode=mode,
        )

    def save_chat_message(self, session_id: str, role: str, content: str, mode: str, emotion: Optional[str] = None):
        self.personal.save_chat_message(session_id, role, content, mode, emotion)

    @property
    def should_extract(self) -> bool:
        return self.personal.should_extract

    # ── 工作知识库包装 ───────────────────────────────────────

    async def add_knowledge_chunk(
        self,
        document_id: str,
        content: str,
        chunk_index: int = 0,
        workspace_id: Optional[str] = None,
        embedding: Optional[bytes] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        return await self.knowledge.add_chunk(
            document_id=document_id,
            content=content,
            chunk_index=chunk_index,
            workspace_id=workspace_id,
            embedding=embedding,
            metadata=metadata,
        )

    async def search_knowledge_chunks(
        self,
        query: str,
        workspace_id: Optional[str] = None,
        document_id: Optional[str] = None,
        top_k: int = 5,
    ) -> list[dict]:
        return await self.knowledge.search_chunks(
            query=query,
            workspace_id=workspace_id,
            document_id=document_id,
            top_k=top_k,
        )


class ActiveConcern:
    """主动关怀系统 — 双引擎主动互动体系。

    引擎 A（对话触发）：
        - 每次用户发言 → LLM 情绪检测 → 有信号则入 concern_queue
        - 立即生成关怀回复 → 发送
        - 下次聊天时检查 pending 队列并复查跟进
        - 解析闭环：正面回复 → resolved；7天超时 → expired

    引擎 B（空闲触发）：
        - 用户长时间未发言 → 优先复查 pending 关怀 → 否则 LLM + 记忆闲聊
        - 复用不新建 IdleChatEngine，由 chat.py 管理 timer 生命周期
    """

    def __init__(self):
        self._emotion_detector = None  # 懒加载

    # ── 向后兼容接口（保留旧日常关怀使用）─────────────────

    def should_fire(self, trigger: str = "first_chat", mode: str = "daily") -> bool:
        """检查今天是否已经触发过指定类型的关怀（每日去重）。"""
        today_ms = _db.get_today_start_ms()
        recent = _db.get_recent_concern(trigger, mode, since_ms=today_ms)
        return recent is None

    def record(self, trigger: str, content: str, mode: str = "daily"):
        """记录一次关怀触发（写入 active_concern 表）。"""
        _db.add_concern(trigger, content, mode)

    # ── 引擎 A：情绪检测 + 关怀队列 ───────────────────────

    def _get_detector(self):
        if self._emotion_detector is None:
            from app.core.concern.emotion_detector import get_emotion_detector
            self._emotion_detector = get_emotion_detector()
        return self._emotion_detector

    async def detect_and_queue(
        self,
        provider,
        user_text: str,
        mode: str = "daily",
    ) -> dict:
        """引擎 A 核心：LLM 情绪检测 + 入队 + 返回关怀结果。

        Returns:
            {
                "concern_id": str | None,    # 关怀项 ID（无信号时为 None）
                "care_text": str | None,     # 立即关怀文本（无信号时为 None）
                "signal": EmotionSignal,     # 检测信号
            }
        """
        detector = self._get_detector()
        signal = await detector.detect(provider, user_text)

        if not signal.detected:
            return {"concern_id": None, "care_text": None, "signal": signal}

        # 创建关怀队列记录
        import uuid
        concern_id = f"c_{uuid.uuid4().hex[:12]}"
        now_ms = int(time.time() * 1000)
        expires_ms = now_ms + 7 * 86400_000  # 7 天过期

        _db.add_concern_queue(
            concern_id=concern_id,
            concern_type=signal.concern_type,
            detail=signal.detail,
            severity=signal.severity,
            expires_at=expires_ms,
            mode=mode,
        )

        return {
            "concern_id": concern_id,
            "care_text": signal.suggested_care,
            "signal": signal,
        }

    async def check_pending(self, mode: str = "daily") -> list[dict]:
        """获取当前未完成的关怀队列（按最早优先）。"""
        return _db.get_pending_concerns(mode=mode)

    def resolve_concern(self, concern_id: str) -> bool:
        """将关怀项标记为已解决（用户正面回复后调用）。"""
        return _db.update_concern_status(concern_id, "resolved")

    def expire_stale(self, mode: str = "daily") -> int:
        """将过期关怀项标记为 expired。返回更新的数量。"""
        return _db.expire_stale_concerns(mode=mode)

    def mark_checked(self, concern_id: str) -> bool:
        """标记关怀项已被复查一次。"""
        return _db.check_concern(concern_id)

    # ── 引擎 B：主动聊天内容生成 ─────────────────────────

    async def generate_proactive_content(
        self,
        provider,
        mode: str = "daily",
        idle_minutes: int = 45,
        recalled_memories: list[dict] | None = None,
    ) -> str | None:
        """引擎 B 内容生成：优先 pending 复查 → 否则 LLM + 记忆闲聊。

        Returns:
            要发送的聊天文本，或 None（表示跳过）
        """
        import logging
        from app.core.llm.base import LLMMessage

        logger = logging.getLogger("active_concern")

        # 优先级 1：有 pending 关怀 → 生成复查问候
        pending = _db.get_pending_concerns(mode=mode, limit=1)
        if pending:
            concern = pending[0]
            follow_up = await self._generate_follow_up(provider, concern)
            if follow_up:
                self.mark_checked(concern["id"])
                # 复查问候已发送 → 解除 pending，下次不再重复
                self.resolve_concern(concern["id"])
                return follow_up

        # 优先级 2：结合记忆闲聊
        idle_text = await self._generate_idle_casual(
            provider, idle_minutes, recalled_memories
        )
        return idle_text

    async def _generate_follow_up(self, provider, concern: dict) -> str | None:
        """对单个 pending 关怀项生成复查问候。"""
        import logging
        from app.core.llm.base import LLMMessage
        import json

        try:
            from app.core.concern.prompts import get_concern_prompts
            template = get_concern_prompts().concern_follow_up

            # 格式化时间
            last_checked = concern.get("lastCheckedAt")
            time_str = "刚才" if not last_checked else f"{last_checked}"

            prompt = template.format(
                concern_detail=concern["detail"],
                last_checked=time_str,
            )

            response = await provider.chat(
                [
                    LLMMessage(role="system", content=prompt),
                    LLMMessage(role="user", content="请生成复查问候"),
                ],
                temperature=0.8,
                max_tokens=128,
            )
            return response.content.strip() or None
        except Exception as e:
            logging.getLogger("active_concern").debug("生成复查问候失败: %s", e)
            return None

    async def _generate_idle_casual(
        self,
        provider,
        idle_minutes: int,
        recalled_memories: list[dict] | None,
    ) -> str | None:
        """生成空闲闲聊内容。每次随机注入风格指令以保证多样性。"""
        import logging
        import random
        from app.core.llm.base import LLMMessage

        logger = logging.getLogger("active_concern")

        # 随机风格注入 — 每次 LLM 收到的指令不同，杜绝重复输出
        _IDLE_STYLES = [
            "用撒娇的语气打招呼，像刚睡醒一样迷迷糊糊的",
            "分享一个有趣但冷门的小知识（科学/历史/自然都行）",
            "表达自己想出去玩的愿望，描述想去的地方",
            "问一个有趣的假设性问题让对方思考",
            "用诗意的语言描述此刻的夜晚或窗外",
            "回忆一段虚构的温柔小故事，像童话一样",
            "假装自己刚刚经历了一件小事，兴奋地分享",
            "表达想念的心情，但不直接说'我想你'",
            "吐槽一些生活中令人无奈的小事，带点幽默",
            "像发现新大陆一样，分享一件刚学到的东西",
            "用第二人称写一句像小说开头的对白",
            "假装偷看对方在做什么，带着调皮的好奇心",
            "分享一个关于萤火虫或星星的浪漫小秘密",
            "关心对方的梦想或愿望，轻轻地追问",
            "用自言自语的语气说些不着边际的牢骚",
        ]

        style = random.choice(_IDLE_STYLES)

        try:
            from app.core.concern.prompts import get_concern_prompts
            system_text = get_concern_prompts().idle_casual.format(
                idle_minutes=idle_minutes,
            )

            # 记忆上下文（可选）
            memory_hint = ""
            if recalled_memories:
                mem_lines = []
                for m in recalled_memories[:3]:
                    mem_lines.append(f"- {m.get('content', '')}")
                memory_hint = "\n可能相关的记忆：\n" + "\n".join(mem_lines)

            logger.debug("[空闲闲聊] style=%s idle=%dmin memories=%d",
                         style[:15], idle_minutes, len(recalled_memories or []))

            response = await provider.chat(
                [
                    LLMMessage(role="system", content=system_text),
                    LLMMessage(
                        role="user",
                        content=(
                            f"你当前处于{'日常' if mode == 'daily' else '工作'}模式。"
                            f"风格要求：{style}。"
                            f"用「你」称呼对方，禁止「用户君」「主人」等。50字左右。"
                            f"{memory_hint}"
                        )
                    ),
                ],
                temperature=1.2,
                max_tokens=128,
            )

            content = response.content.strip() if response.content else ""
            thinking = response.thinking.strip() if response.thinking else ""

            logger.debug("[空闲闲聊] LLM 返回: content=%d字 thinking=%d字",
                         len(content), len(thinking))

            if not content and thinking:
                # 模型只输出了 thinking 没有正文 → 取 thinking 前80字作为正文
                logger.warning("[空闲闲聊] LLM 只返回了 thinking，使用 thinking 作为正文")
                return thinking[:80]

            return content or None

        except Exception as e:
            logger.warning("生成空闲闲聊失败: %s", e)
            return None

    def clear_cache(self):
        """清除 LLM prompt 模板缓存（配置更新后调用）。"""
        self._emotion_detector = None
        from app.core.concern.emotion_detector import get_emotion_detector
        get_emotion_detector().clear_cache()
        from app.core.concern.prompts import get_concern_prompts
        get_concern_prompts().clear_cache()


# 模块级单例（保持向后兼容）
MemoryManager = MemoryFacade  # 类型别名保持别名兼容
memory_manager = MemoryFacade()
active_concern = ActiveConcern()
