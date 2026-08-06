"""QQ 通道协议注入集成测试（账本 #7 / 验收 #2）。

走完整 WS 链路（Mock LLM）：channel=="qq" 时 author's note 区追加
QQ 协议块（严格消息格式 + 档位上限）；无 channel（桌面端）不注入。
"""
import json


def _last_author_note(provider) -> str:
    """取发给 LLM 的最后一条 system 消息（author's note 区）。"""
    for m in reversed(provider.last_messages):
        if m.role == "system":
            return m.content
    return ""


def _chat_once(api_client, content: str, session_id: str, channel: str = ""):
    with api_client.websocket_connect("/ws/chat") as ws:
        ws.send_text(json.dumps({"type": "voice_toggle", "enabled": False}))
        payload = {"type": "chat", "content": content, "sessionId": session_id}
        if channel:
            payload["channel"] = channel
        ws.send_text(json.dumps(payload))
        while True:
            msg = json.loads(ws.receive_text())
            t = msg.get("type")
            if t == "done":
                return
            if t == "error":
                raise AssertionError(f"WS 返回错误: {msg}")


def test_qq_channel_injects_protocol(api_client, provider):
    _chat_once(api_client, "你好", "sess-qq", channel="qq")
    an = _last_author_note(provider)
    assert "QQ 手机聊天协议" in an
    assert "禁止动作描写" in an
    assert "暧昧暗示" in an


def test_non_qq_channel_no_protocol(api_client, provider):
    _chat_once(api_client, "你好", "sess-desk")
    an = _last_author_note(provider)
    assert "QQ 手机聊天协议" not in an
