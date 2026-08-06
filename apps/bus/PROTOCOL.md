# 桌宠 WS 协议（bus ↔ 桌宠客户端）— 供 C 包 T-06 实现

> 依据：CONTRACTS §0.2（桌宠 = 总线客户端，连 bus 而非 companion）+ §13.4（说做分离）。
> 版本：v1（T-03 定义，2026-08-06）

## 连接

```
ws://<bus-host>:8767/ws/desktop      # BUS_WS_PORT 可配
```

- 连接即视为桌宠在线（可达性 10s×3 置信由 bus 侧 `ReachabilityTracker` 处理）。
- 断线后 bus 侧 30s 超时判离线；期间投递自动降级下一通道（QQ 兜底）。

## 消息格式（JSON，UTF-8）

### 桌宠 → bus

| type | 字段 | 说明 |
|---|---|---|
| `chat` | `content: str`（必填）、`sessionId?: str`、`refId?: str` | 用户消息入 inbox（source=desktop），bus 回复 `ack` |
| `heartbeat` | — | 10s 周期心跳（驱动可达性） |
| `mode_switch` | `mode: "daily"\|"work"`（必填） | 全局模式切换（CONTRACTS §13.2：仅桌宠端可切）；生效回 `mode_switched` |
| `voice_input` | — | **占位**：语音输入（C-3 后置，本期不实现） |

### bus → 桌宠

| type | 字段 | 说明 |
|---|---|---|
| `proactive_speech` | `content: str`、`source: "bus"`、`refId?: str` | 主动消息/回复推送（文字） |
| `voice_audio` | `audioUrl?: str`、`audioBase64?: str`、`text?: str` | 语音（TTS 接线 D3 后置） |
| `device_command` | `command: {id, kind, payload}` | 说做分离动作（§13.4），桌宠执行（打开导航/播放/通知等） |
| `mode_switched` | `mode: "daily"\|"work"` | mode_switch 生效确认（companion 全局模式已切） |
| `ack` | `messageId: str` | 用户消息已入 inbox 确认 |
| `error` | `message: str` | 错误（如 content 为空、mode 非法/冷却中） |

### 示例

```jsonc
// 桌宠 → bus：用户消息
{"type":"chat","content":"到家了","sessionId":"desktop-u1"}

// bus → 桌宠：主动消息 + 语音 + 动作（一次生成可带多条）
{"type":"proactive_speech","content":"欢迎回来，星～","source":"bus","refId":"hub-12"}
{"type":"voice_audio","audioUrl":"http://127.0.0.1:8765/api/voice/file/abc.wav","text":"欢迎回来，星～"}
{"type":"device_command","command":{"id":"m9","kind":"open_app","payload":{"app":"music"}}}
```

## 会话

- 用户消息带 `sessionId`（建议 `desktop-<uuid>`，CONTRACTS §4 端隔离）；缺省时 bus 用 `desktop-default` 兜底。
- bus 侧生成桥调用 companion 时透传该 sessionId，UI 历史与 QQ 端互相独立。

## 边界

- bus 只负责路由/生成调度/派发；桌宠 UI 渲染、Live2D、本地功能由 T-06 实现。
- 心跳断开重连由桌宠侧负责（10s 周期 + 指数退避）。
