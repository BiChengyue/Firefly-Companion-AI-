import asyncio
import json
import sys
import uuid
import websockets

async def test_ws_chat(message_text: str, session_id: str, mode: str = "daily"):
    url = "ws://127.0.0.1:8765/ws/chat"
    print(f"[*] Connecting to {url} ...")
    
    try:
        async with websockets.connect(url) as websocket:
            print("[+] Connected successfully!")
            
            # Send mode switch if we want work mode
            if mode != "daily":
                print(f"[*] Switching mode to: {mode}")
                await websocket.send(json.dumps({
                    "type": "mode_switch",
                    "mode": mode
                }))
                # Wait briefly for server config acknowledgement
                resp = await websocket.recv()
                print(f"[v] Server config response: {resp}")
            
            # Send chat message
            chat_payload = {
                "type": "chat",
                "content": message_text,
                "sessionId": session_id
            }
            print(f"[*] Sending message: {json.dumps(chat_payload, ensure_ascii=False)}")
            await websocket.send(json.dumps(chat_payload))
            
            print("\n--- Stream Response ---")
            
            # Read responses
            async for raw_msg in websocket:
                msg = json.loads(raw_msg)
                msg_type = msg.get("type")
                
                if msg_type == "token":
                    # Print token delta directly to stdout
                    sys.stdout.write(msg.get("delta", ""))
                    sys.stdout.flush()
                elif msg_type == "thinking":
                    # Thinking block (gray text effect in compatible terminals)
                    sys.stdout.write(f"\033[90m{msg.get('delta', '')}\033[0m")
                    sys.stdout.flush()
                elif msg_type == "emotion":
                    print(f"\n\n[Emotion] => {msg.get('label')}")
                elif msg_type == "meme":
                    data_len = len(msg.get("data", ""))
                    print(f"[Meme] => Triggered. Payload base64 len: {data_len} bytes")
                elif msg_type == "concern":
                    print(f"[Proactive Care] => {msg.get('content')}")
                elif msg_type == "done":
                    print("\n[Done] => Assistant finished responding.")
                    break
                elif msg_type == "error":
                    print(f"\n[Error] => {msg.get('message')}")
                    break
                else:
                    print(f"\n[Unknown Message] => {msg}")
            
            print("-----------------------\n")
            
    except ConnectionRefusedError:
        print("[!] Connection refused! Is the FastAPI server running at 127.0.0.1:8765?")
    except Exception as e:
        print(f"[!] Error: {e}")

if __name__ == "__main__":
    msg = "你好，流萤！请问你最喜欢的食物是橡木蛋糕卷吗？"
    session = f"test-sess-{uuid.uuid4().hex[:6]}"
    
    if len(sys.argv) > 1:
        msg = sys.argv[1]
    if len(sys.argv) > 2:
        session = sys.argv[2]
        
    mode = "daily"
    if "--work" in sys.argv:
        mode = "work"
        # remove flag
        sys.argv.remove("--work")
        
    print(f"[*] Starting test with session={session}, mode={mode}")
    asyncio.run(test_ws_chat(msg, session, mode))
