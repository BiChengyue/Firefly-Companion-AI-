

def test_split_reply_chunks():
    from bus.dispatcher import split_reply_chunks

    # 短句不分条
    assert split_reply_chunks("嗯，好的~") == ["嗯，好的~"]
    # 长回复拆条
    long = "今天也想你了，星宝。*微微脸红* 要不要一起去看海？那边的日落特别好看。"
    chunks = split_reply_chunks(long)
    assert len(chunks) >= 2
    assert "".join(chunks).replace(" ", "") == long.replace(" ", "")  # 不丢内容
    # 超长 → 最多 4 条
    very_long = "。".join([f"这是第{i}句很长的内容" for i in range(20)]) + "。"
    chunks = split_reply_chunks(very_long)
    assert len(chunks) <= 4
    assert "".join(chunks) == very_long
    # 空内容
    assert split_reply_chunks("") == [""]


def test_dispatch_splits_chunks(monkeypatch):
    """dispatch 时拆条：adapter 收到多条内容，且拼接后等于整段。"""
    from bus.dispatcher import Dispatcher
    from bus.models import DeliverySequence, DeliveryPolicy, DeliveryChannel, OutboundMessage

    received = []

    class FakeStore:
        def get_inbound(self, mid):
            return {
                "id": mid, "status": "pending",
                "sequence": DeliverySequence(
                    messageId=mid,
                    targets=[DeliveryChannel.DESKTOP],
                    policy=DeliveryPolicy.FIXED,
                ),
            }

        def _outbound(self, mid):  # 未用
            return None

        def mark_outbound(self, mid, status, attempts):
            pass

        def mark_inbound(self, mid, status):
            pass

        def get_delivered_chunks(self, mid):
            return getattr(self, "_delivered", {})

        def set_delivered_chunks(self, mid, chunks):
            self._delivered = chunks

    class FakeAdapter:
        def deliver(self, channel, message):
            received.append(message.content)
            return True

    class FakeDispatcher(Dispatcher):
        def __init__(self):
            self.store = FakeStore()
            self.adapter = FakeAdapter()

        def _outbound_for(self, mid, channel):
            return OutboundMessage(id=mid, target=channel, content=(
                "第一句，想你。第二句，也想你。第三句，还是很想你。"
            ))

        def _attempts(self, mid):
            return 0

    d = FakeDispatcher()
    d.dispatch("m1")
    assert len(received) >= 2
    assert "".join(received) == "第一句，想你。第二句，也想你。第三句，还是很想你。"


def test_split_reply_chunks_newline_priority():
    """LLM 用换行分条 → 按换行拆（用户指示：输出总线按条发送）。"""
    from bus.dispatcher import split_reply_chunks

    multi = "今天也想你了，星宝~\n\n要不要一起去看海？日落很好看。\n\n晚上我等你回来。"
    chunks = split_reply_chunks(multi)
    assert chunks == ["今天也想你了，星宝~", "要不要一起去看海？日落很好看。", "晚上我等你回来。"]

    # 超 4 条 → 合并末条
    over = "\n\n".join([f"第{i}条" for i in range(6)])
    chunks = split_reply_chunks(over)
    assert len(chunks) <= 4
    assert "第5条" in chunks[-1]

    # 短内容原样
    assert split_reply_chunks("嗯，好的~") == ["嗯，好的~"]


def test_dispatch_work_mode_no_split(monkeypatch):
    """work 模式（萨姆）禁止分条：即使内容含换行也整条发送。"""
    from bus.dispatcher import Dispatcher
    from bus.models import DeliverySequence, DeliveryPolicy, DeliveryChannel, OutboundMessage

    from bus.dispatcher import Dispatcher
    from bus.models import DeliverySequence, DeliveryPolicy, DeliveryChannel, OutboundMessage, OutboundVoice

    received = []

    class FakeStore:
        def get_inbound(self, mid):
            return {
                "id": mid, "status": "pending",
                "sequence": DeliverySequence(
                    messageId=mid, targets=[DeliveryChannel.DESKTOP],
                    policy=DeliveryPolicy.FIXED,
                ),
            }

        def mark_outbound(self, mid, status, attempts):
            pass

        def mark_inbound(self, mid, status):
            pass

        def get_delivered_chunks(self, mid):
            return getattr(self, "_delivered", {})

        def set_delivered_chunks(self, mid, chunks):
            self._delivered = chunks

    class FakeAdapter:
        def deliver(self, channel, message):
            received.append(message.content)
            return True

    class FakeDispatcher(Dispatcher):
        def __init__(self):
            self.store = FakeStore()
            self.adapter = FakeAdapter()

        def _outbound_for(self, mid, channel):
            return OutboundMessage(
                id=mid, target=channel, mode="work",
                content="目标确认，开始扫描。\n\n坐标已锁定，执行指令。",
            )

        def _attempts(self, mid):
            return 0

    d = FakeDispatcher()
    d.dispatch("m2")
    assert len(received) == 1  # work 模式不分条
    assert "\n\n" in received[0]


def test_dispatch_chunk_retry_skips_delivered(monkeypatch):
    """分条幂等（T-26 🟠4）：失败重试跳过已送达 chunk，不重复投递。

    首次投递第 1 条成功、第 2 条失败 → status=failed；
    重试时 delivered_chunks 记录已送达 1 条 → 只投剩余 3 条。
    """
    from bus.dispatcher import Dispatcher
    from bus.models import DeliverySequence, DeliveryPolicy, DeliveryChannel, OutboundMessage, OutboundVoice

    received = []
    state = {"failures": 1}

    class FakeStore:
        def get_inbound(self, mid):
            return {
                "id": mid, "status": "pending",
                "sequence": DeliverySequence(
                    messageId=mid, targets=[DeliveryChannel.DESKTOP],
                    policy=DeliveryPolicy.FIXED,
                ),
            }

        def mark_outbound(self, mid, status, attempts):
            pass

        def mark_inbound(self, mid, status):
            pass

        def get_delivered_chunks(self, mid):
            return getattr(self, "_delivered", {})

        def set_delivered_chunks(self, mid, chunks):
            self._delivered = chunks

    class FakeAdapter:
        def deliver(self, channel, message):
            received.append(message.content)
            if len(received) == 2 and state["failures"] > 0:
                state["failures"] -= 1
                return False  # 第 1 条送达后，第 2 条失败（验证重试跳过已送达的第 1 条）
            return True

    class FakeDispatcher(Dispatcher):
        def __init__(self):
            self.store = FakeStore()
            self.adapter = FakeAdapter()

        def _outbound_for(self, mid, channel):
            return OutboundMessage(
                id=mid, target=channel,
                content="第一句。\n\n第二句。\n\n第三句。\n\n第四句。",
            )

        def _attempts(self, mid):
            return 0

    d = FakeDispatcher()
    # 首次：第 1 条送达，第 2 条尝试失败 → failed
    acks1 = d.dispatch("m1")
    assert acks1[0].status == "failed"
    assert received.count("第一句。") == 1
    # 重试：跳过已送达的第 1 条（不重复），投剩余 2/3/4 → delivered
    acks2 = d.dispatch("m1")
    assert acks2[0].status == "delivered"
    assert received.count("第一句。") == 1  # 已送达的绝不再投
    assert received.count("第二句。") == 2  # 失败条在重试时补投
    assert received[-2:] == ["第三句。", "第四句。"]


def test_dispatch_chunk_passes_refid_voice(monkeypatch):
    """分条 chunk 透传 refId/voice（T-26 🟠5）：语音推送与主动消息关联不因分条断裂。"""
    from bus.dispatcher import Dispatcher
    from bus.models import DeliverySequence, DeliveryPolicy, DeliveryChannel, OutboundMessage, OutboundVoice

    received = []

    class FakeStore:
        def get_inbound(self, mid):
            return {
                "id": mid, "status": "pending",
                "sequence": DeliverySequence(
                    messageId=mid, targets=[DeliveryChannel.DESKTOP],
                    policy=DeliveryPolicy.FIXED,
                ),
            }

        def mark_outbound(self, mid, status, attempts):
            pass

        def mark_inbound(self, mid, status):
            pass

        def get_delivered_chunks(self, mid):
            return getattr(self, "_delivered", {})

        def set_delivered_chunks(self, mid, chunks):
            self._delivered = chunks

    class FakeAdapter:
        def deliver(self, channel, message):
            received.append(message)
            return True

    class FakeDispatcher(Dispatcher):
        def __init__(self):
            self.store = FakeStore()
            self.adapter = FakeAdapter()

        def _outbound_for(self, mid, channel):
            return OutboundMessage(
                id=mid, target=channel,
                content="第一句。\n\n第二句。",
                refId="ref-123",
                voice=OutboundVoice(audioUrl="http://x/a.wav", text="第一句。", durationMs=1000),
            )

        def _attempts(self, mid):
            return 0

    d = FakeDispatcher()
    d.dispatch("m2")
    assert len(received) == 2
    assert all(m.refId == "ref-123" for m in received)
    # voice 只随第一条 chunk（2026-08-07：同一完整语音不随分条播 N 遍）
    assert received[0].voice is not None
    assert all(m.voice is None for m in received[1:])


def test_dispatch_chunk_matches_voices_list(monkeypatch):
    """分条语音（2026-08-07）：voices 列表与文字分条顺序一致——chunk i 配 voices[i]。"""
    from bus.dispatcher import Dispatcher
    from bus.models import DeliverySequence, DeliveryPolicy, DeliveryChannel, OutboundMessage, OutboundVoice

    received = []

    class FakeStore:
        def get_inbound(self, mid):
            return {
                "id": mid, "status": "pending",
                "sequence": DeliverySequence(
                    messageId=mid, targets=[DeliveryChannel.DESKTOP],
                    policy=DeliveryPolicy.FIXED,
                ),
            }

        def mark_outbound(self, mid, status, attempts):
            pass

        def mark_inbound(self, mid, status):
            pass

        def get_delivered_chunks(self, mid):
            return getattr(self, "_delivered", {})

        def set_delivered_chunks(self, mid, chunks):
            self._delivered = chunks

    class FakeAdapter:
        def deliver(self, channel, message):
            received.append(message)
            return True

    class FakeDispatcher(Dispatcher):
        def __init__(self):
            self.store = FakeStore()
            self.adapter = FakeAdapter()

        def _outbound_for(self, mid, channel):
            return OutboundMessage(
                id=mid, target=channel,
                content="第一句。\n\n第二句。\n\n第三句。",
                voices=[
                    OutboundVoice(audioUrl="http://x/1.wav", text="第一句。"),
                    OutboundVoice(audioUrl="http://x/2.wav", text="第二句。"),
                    OutboundVoice(audioUrl="http://x/3.wav", text="第三句。"),
                ],
            )

        def _attempts(self, mid):
            return 0

    d = FakeDispatcher()
    d.dispatch("m3")
    assert len(received) == 3
    assert [m.voice.audioUrl if m.voice else None for m in received] == [
        "http://x/1.wav", "http://x/2.wav", "http://x/3.wav",
    ]
    assert [m.content for m in received] == ["第一句。", "第二句。", "第三句。"]
