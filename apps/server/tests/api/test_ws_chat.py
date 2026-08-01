import json


def test_ws_chat_echo(api_client):
    """对话主链路：发闲聊 -> Mock LLM 流式返回 -> 收到 token 与 done。"""
    with api_client.websocket_connect("/ws/chat") as ws:
        # 先关闭 TTS，避免测试触发真实语音合成
        ws.send_text(json.dumps({"type": "voice_toggle", "enabled": False}))
        ws.send_text(
            json.dumps({"type": "chat", "content": "你好", "sessionId": "sess-test"})
        )

        full = ""
        while True:
            msg = json.loads(ws.receive_text())
            t = msg.get("type")
            if t == "token":
                full += msg.get("delta", "")
            elif t == "done":
                break
            elif t == "error":
                raise AssertionError(f"WS 返回错误: {msg}")

    assert full == "你好呀，我是流萤~"
