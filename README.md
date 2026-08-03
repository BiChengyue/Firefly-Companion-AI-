# Firefly Companion — 流萤桌面 Live2D AI 伴侣

> 基于《崩坏：星穹铁道》角色「流萤」的跨平台（Windows / macOS / Linux）桌面智能 AI 伴侣系统。
>
> 流萤以 Live2D 形态常驻桌面，融合双窗口解耦与鼠标穿透技术、流式 AI 对话、本地 ONNX 语义记忆、双引擎主动关怀、GPT-SoVITS 流萤原声 TTS、Agent 任务执行、MCP 开放工具链与 Skill 插件体系。

> [!IMPORTANT]
> **版权与合规声明**：本项目涉及的角色形象与游戏素材版权均归 **米哈游 (miHoYo)** 所有。项目完全免费开源，仅供个人学习交流使用，请勿用于任何商业用途。

---

## 🎬 功能演示视频

> 想快速了解它能做什么？欢迎观看完整功能演示视频：

- 📺 [点击观看《流萤桌面 AI 伴侣 · 功能演示》]()（占位，待补充视频链接）

> 视频将带你依次体验：双模式切换、Live2D 交互、本地记忆、主动关怀、语音（流萤原声 TTS）、Agent 任务执行、提醒 / 天气 / 头像管理、Skill 与 MCP 扩展等全部功能。

---

## 👤 面向普通用户：快速上手

> 这一节带你以"使用者"视角快速了解并上手。想深入技术实现或参与开发，请直接跳到下方「🔬 面向开发者：技术特性」一节。

### 🎭 两种模式：日常 vs 工作

- **日常模式（流萤）**：温馨的萤火虫绿主题，柔和毛玻璃 UI。以**流萤的身份陪你聊天**，你可以和她聊剧情、聊日常。
- **工作模式（萨姆）**：暗黑机甲 HUD 风格。以**萨姆的身份执行 Agent 任务**，展示 AI 推理思维链全过程。

> 💡 **一句话区分**：日常模式主打聊天陪伴，工作模式主打任务执行。日常模式下默认**不使用工具**（仅聊天），如需临时让流萤干活，见下方「解除限制」。

### 🔓 日常模式解除限制（工具中心）

- 入口在 **工具中心**（也是系统设置页面）中的「解除限制」按钮。
- 打开后，日常模式下的流萤也能执行 Agent 任务（网页查询等简单操作）。
- ⚠️ **注意**：解禁状态下**请勿进行剧情讨论**——Agent 工具可能介入，破坏聊天体验。想聊剧情请在**非解禁**的日常模式下进行。
- **Agent 控制台**：一个 HUD 面板，用于显示 Agent 任务执行与工作调度的过程，可点击常驻在聊天页。日常模式默认用不到，只有工作模式或日常解禁状态下才会动用。

> 🛠️ **工具中心 / WebSocket 设置**：工具中心打开后显示的就是系统设置页面。其中的 **WebSocket 端口 8765** 用于前后端实时通信，**重连延迟**是断线后自动重连的间隔——这两项保持默认数值即可，正常无需改动。

### 🎵 工作模式 BGM

- 日常模式切换到工作模式时，会触发「永不复焉」BGM 播放。

### 💬 流萤主动发起对话

- 可在设置里开启，设定一个空闲时长（例如 10 分钟）。
- 若该时长内没有和你聊天，流萤会**主动找你聊天**，Live2D 模型上方也会弹出相应提醒。

### 🖼️ 头像管理

- 可以自由添加 / 删除流萤的头像，并在此更换。
- 注意：**日常模式与工作模式的角色头像是不一样的**，可分别管理。
- 头像来源主要为网络收集 + AI 生成图片。

### 💬 会话管理

- 类似其他大模型网页的会话面板：可**自由添加 / 删除会话**，**双击可重命名**会话。
- 建议在长时间聊天后新建会话，让流萤保持最佳状态（见下方「⚠️ 使用建议与局限性」）。

### 📂 工作空间

- Agent 的默认工作路径。生成或编辑文件等操作都会在工作空间中进行。
- 可自由选定文件位置，相关操作说明见下方开发者区的「工作空间」一节。

### 🧩 Skill 与 MCP 扩展

- 在 **扩展** 处可导入 **用户 Skill** 与 **MCP 服务**。
- Skill 支持直接导入 / 点击查看和调用，详见下方开发者区的「Skill 插件体系」一节。

### 🌤️ 天气模块

- 右侧面板的天气组件，输入对应城市即可实时查询天气。

### ⏰ 提醒模块（与流萤互动）

- 支持自然语言提醒，例如输入"提醒我 10 秒后休息"。
- 到点后 Live2D 模型会提示你"时间到了"，你可以选择**结束**或**延长**提醒。
- 详情见下方开发者区的「自然语言提醒」一节。

### 🧠 记忆管理

- 右侧面板的 **记忆管理** 模块，管理模型对用户的记忆。
- 流萤会通过聊天自动提取记忆（如"我喜欢许嵩的音乐""我喜欢唐朝历史"），换一个会话仍会记得。
- 支持手动添加 / 编辑 / 删除记忆；卡片旁的**数字是置信度**——代表模型记得多清楚，会随时间衰减变小，但**多次提醒会更新这些数据**，不同记忆的遗忘速度也不同。
- 注意：**日常模式与工作模式的记忆空间是隔离的**，而"全局共享偏好"是双方空间共享的。

---

## 🗣️ 语音模式（三选一）

流萤支持三种语音方式：**Edge-TTS**、**MiniMax** 和 **GPT-SoVITS**（流萤原声）。在系统设置 → 语音中切换。

| 方式 | 是否需要下载 | 音色 | 说明 |
| :--- | :--- | :--- | :--- |
| **Edge-TTS** | 无需下载 | 与流萤无关 | 开箱即用，纯"听个响"，音效与流萤八竿子打不着 |
| **MiniMax** | 需 API 接入 | 可自定义 | 需自行上传并配置音色，接入 MiniMax 服务 |
| **GPT-SoVITS** | 需下载（体积较大） | **流萤原声** | 本地模型，已训练好的流萤声音，效果最佳 |

### ⚙️ GPT-SoVITS 使用说明

- ⚠️ **内存占用**：GPT-SoVITS 会占用约 **2GB 后台内存**，**电脑内存小的不建议开启**。
- **权重文件**：项目内已包含权重文件；因空间过大，其余文件需自行下载。
- **环境配置**：已配置好环境脚本，打开引擎目录点击对应文件等待下载即可（下载时间较长、体积大，请耐心等待）。下载完成后还会提示下载缺少的 **SoVITS V4**——两者都下载好会显示「Python 推理环境已就绪」。
- **拉起引擎**：点击「拉起原声引擎」，完成后会显示「已拉起」。可用「测试」按钮确认声音模型是否在工作。
- **释放内存**：若觉得内存占用过大，可点击「释放内存」。
- **清理缓存**：模型生成的语音文件超过 **200MB** 会自动清理，也可点击按钮手动清理。

---

## ⚙️ 模型配置注意事项

- **Provider 选择**：根据你的供应商自由选择即可，模型已预选，缺少的模型可直接手动填写。
- **自定义模型**：需支持 **OpenAI Chat Completions API** 接口格式，并按官方文档进行配置。
- **Thinking 模式**：开启前务必确认模型是否支持 `thinking` 参数，**不支持时请勿开启，否则容易报错**。
- **诊断**：系统设置中提供「诊断」功能，用于测试模型连接是否成功。

---

## ⚠️ 使用建议与局限性

感谢你的理解——本项目由作者以 **Vibe Coding** 方式独立开发，代码与架构相对稚嫩，未做过极端压力测试。以下为已知的体验边界：

- **体验依赖底层 API 大模型能力**：随着上下文拉长，大模型不可避免会出现**注意力分散或幻觉（角色飘逸）**的情况。
- **建议**：长时间聊天时，**适时新建会话**，让流萤保持最佳状态。作者也在思考相关对策，防止角色飘逸问题发生。
- **任务边界**：本项目主要功能在于**聊天陪伴**，不建议使用 Agent 执行复杂任务；简单的操作（如网页查询）可用。
- **维护节奏**：作者平时事务较多，后续不一定能快速修复所有问题，敬请谅解。
- **共建**：代码已全部开源，欢迎感兴趣的朋友提 **Issue / PR** 共同完善！

---

## 🔬 面向开发者：技术特性

> 以下内容面向开发者 / 贡献者，介绍底层实现与设计。普通用户可跳过本区。

### 🎭 日常 / 萨姆 双模式

- **日常模式（流萤）**：温馨萤火虫绿主题，柔和毛玻璃 UI，提供贴心日常陪伴（正常是不能使用工具，只提供聊天服务，系统设置支持提供解除限制）
- **萨姆战术模式**：暗黑机甲 HUD 风格，展示 AI 推理思维链全过程，显示CPU占用情况
- **Thinking 实时可视化**：解析大模型 `thinking` / `reasoning_content` 输出，在 SAM HUD 控制台中逐字呈现决策推理
- **模式切换过场**：Glitch 特效 + 流萤→萨姆过渡台词

### 🎨 Live2D 角色交互

- 基于 **PixiJS + pixi-live2d-display** 渲染，支持 Cubism 2 / 4 模型
- **表情系统**：11 种表情（回正 / 墨镜 / 猫耳 / 难受 / 鄙夷 / 生气 / 疑问 / 哭泣 / 流汗 / 呆愣 / 嘻嘻），随对话情绪与主动关怀自动切换
- **动作状态机**：IDLE → SPEAKING ↔ REACTING 三态切换，支持自动眨眼、呼吸动画
- **热区交互**：点击头部切换墨镜，点击耳朵切换猫耳
- **语音同步口型**：TTS 播放时自动驱动嘴部张合
- **物理悬浮拖拽**：主界面提供"解锁桌宠"按钮，松开后可在桌面上自由拖动定位

### 🧠 本地语义记忆系统

**后端引擎：**
- **纯 SQLite 存储**：记忆向量以 BLOB 形式存储在 SQLite 中，不依赖 ChromaDB 等任何外部向量数据库
- **双引擎向量检索**：
  - **ONNX 真语义引擎**：`paraphrase-multilingual-MiniLM-L12-v2`（384 维，~120MB），支持中英双语跨语言语义检索
  - **哈希领域增强引擎**：自研 23 类手工领域知识投影（food / health / game / music / travel...），零模型文件，零外部依赖
  - **混合召回**：ONNX 语义 75% + 哈希领域 25% 加权融合
- **Mem0 记忆生命周期**：ADD / UPDATE / DELETE / IGNORE 判决链，自动去重与冲突合并
- **命名空间隔离**：`shared_profile`（全局共享）/ `daily_life`（日常）/ `work_tasks`（工作）
- **时间衰减遗忘**：15 个 topic 差异化衰减率，旧记忆自动降低权重
- **自适应阈值**：ONNX / 哈希引擎自动切换对应的相似度门限

**管理面板（右侧栏 MemoryWidget）：**
- **按命名空间分组**：🌐 全局共享 / 💼 工作专有 / ☕ 日常记录，手风琴折叠展开，每组默认显示 3 条
- **记忆类型标签**：个人信息 / 偏好 / 事件 / 承诺 / 情感，不同类型以彩色标签区分
- **混合语义搜索**：关键词精确匹配 + 向量语义检索双引擎合并去重
- **置信度可视化**：每条记忆显示置信度百分比，一目了然
- **手动管理**：支持手动添加记忆（选择类型与归属空间）、内联编辑、删除
- **实时同步**：通过 WebSocket `memory_updated` 事件自动刷新，模式切换时自动重载对应空间记忆
- **渐进加载**：展开全部 / 收起分页，避免海量记忆撑爆面板

### 💬 双引擎主动关怀

- **引擎 A — 对话触发**：LLM 实时检测用户情绪（健康 / 情绪 / 事件三维度），自动加入关怀队列并跟进复查
- **引擎 B — 空闲主动聊天**：检测用户长时间未交互时，基于近期记忆生成自然闲聊话题
- **静音时段**：23:00-08:00 自动静默，每日主动聊天次数上限可控
- **Live2D 气泡提示**：主动聊天时角色头顶弹出文字气泡，配合专属动作与表情

### 🗣️ 语音交互

- **TTS 语音合成**（三选一）：
  - **GPT-SoVITS**：本地高保真语音推理，还原流萤音色，支持 CUDA 加速（推理延迟 ~0.3s）
  - **Edge TTS**：轻量离线合成，零配置可用
  - **MiniMax**：云端克隆音色
- **STT 语音识别**：基于 `faster-whisper` 的实时语音转文字
- **音频缓存**：MD5 去重，避免重复合成，同时支持在系统设置语音处释放空间

### 🤖 Agent 任务执行

- **ReAct 循环**：Plan → Execute → Observe → Re-plan，支持步骤中断与断点恢复
- **Token 预算管理**：上下文达 75% 阈值自动压缩
- **安全沙箱**：路径白名单/黑名单 + 命令白名单 + 高风险操作需人工审批
- **内置工具集**：文件读写/搜索/编辑、命令行执行（受控）、浏览器自动化、网络搜索
- **MCP 协议**：支持标准 MCP 插件扩展

### 📂 工作空间

- **本地目录绑定**：将一个本地文件夹设为工作空间，Agent 执行任务时自动切换到该目录作为当前工作目录（CWD）
- **沙箱动态注册**：选中的工作空间路径自动注入 Agent 安全沙箱白名单，所有文件操作限定在该目录范围内
- **多空间管理**：左侧边栏支持创建、切换、删除多个工作空间，内置默认空间不可删除
- **文件夹选择器**：支持 Tauri 原生对话框 / 浏览器 File System API / 手动输入路径三层降级
- **会话关联**：聊天会话可按工作空间筛选，删除空间时自动解除关联

### 🧩 Skill 插件体系

- 兼容 Agent Skills 开放标准
- `SKILL.md` 定义 —— YAML frontmatter 元数据 + Markdown 指令体
- 热重载支持，渐进式披露（元数据 → 全文 → 辅助资源）

### ⏰ 自然语言提醒

- 自动识别"明天上午十点提醒我开会"等自然语言任务
- 相对时间（N 秒/分/小时后）+ 绝对时间（明天 / 日期 + 时间点）
- 系统级跨窗口全息弹窗通知（live2d窗口支持显示），支持 Snooze 稍后提醒（+5 分钟）

### 🛡️ 角色知识库与防幻觉体系

**五层知识金字塔**（注入优先级由高到低）：

| 层 | 信源 | 规模 | 说明 |
|---|---|---|---|
| L0a | `curated_cards/` 精选卡片 | 29 张 | 触发正则强制注入，防严重幻觉 |
| L0b | `facts.yaml` 确定性事实 | 73 条 | 关键词反向匹配，声明式答案 |
| L0c | `firefly_lore.md` 亲历记忆 | 49 块 | 流萤个人经历与自我认知 |
| L1 | `*_lore.md` 世界记忆 | 49 块 | 雅利洛/仙舟/翁法罗斯/二相乐园/黑塔空间站 |
| L2-4 | `resources/hsrchat/` wiki 兜底 | 6294 块 | 全量剧情/角色/NPC 对话 |

**防幻觉机制：**
- **信号闸门**：FTS 预检 + 实体别名表(260 键) + 剧情意图检测 → 闲聊消息零注入
- **视角锚点**：herta 空间站事件独立 header（"据星核猎手任务记录"），其他世界"据开拓者讲述"
- **注入顺序**：facts 先于 narrative，确定性身份锚点后再给叙事细节
- **wiki 清洗**：注入文本剥离【任务名】等游戏 UI 术语，消除"同行任务《XX》"泄露
- **日常模式解锁桥接**：工具可用时自动注入"你依然是流萤本人"规则，防止人格切换

**覆盖范围：** 翁法罗斯 12 黄金裔（含全名别名）、仙舟核心角色（藿藿/桂乃芬/呼雷等）、雅利洛-VI（可可利亚/杰帕德/虎克等）、二相乐园（归寂/隆介/斯科特等）、星穹列车全席（帕姆/三月七等）

**构建工具：** `python apps/server/scripts/build_lore_index.py --force --no-vectors` 一键重建 6717 块索引

### 🔧 大模型灵活接入

- 兼容 **OpenAI / Claude / DeepSeek / 智谱 GLM-4 / 通义千问** 等标准 API
- 可视化设置面板，一键切换 Provider、模型、Temperature、MaxTokens
- Thinking 模式开关（支持智谱 GLM、DeepSeek 等原生 thinking 能力）

---

## 🛠️ 技术栈

| 层级 | 技术 |
| :--- | :--- |
| **桌面外壳** | Tauri 2 + Rust |
| **前端框架** | Vue 3.5 + TypeScript + Vite 6 + Pinia |
| **Live2D 渲染** | PixiJS v7 + pixi-live2d-display (Cubism 2/4) |
| **后端服务** | Python 3.11+ / FastAPI + Uvicorn (ASGI) |
| **通信** | WebSocket (流式对话) + REST API + Tauri Event |
| **数据库** | SQLite（WAL 模式，零外部依赖） |
| **语义向量** | ONNX Runtime (`paraphrase-multilingual-MiniLM-L12-v2`) + 自研哈希引擎 |
| **语音** | GPT-SoVITS + faster-whisper + Edge TTS |
| **配置管理** | pydantic-settings + PyYAML |

---

## 🚀 部署指南

### 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| **操作系统** | Windows 10/11 · macOS 10.15+ · Linux (glibc 2.28+) | 三平台均已适配；macOS 透明桌宠需开启私有 API |
| **Node.js** | 20+ | 前端构建与包管理 |
| **pnpm** | 9+ | Monorepo 包管理器 |
| **Rust** | 1.75+ | Tauri 2 编译（含 cargo + MSVC Build Tools） |
| **Python** | 3.11+ | AI 后端服务 |
| **WebView2** | — | Windows 10 需手动安装，Win11 已内置 |

> **注意**：三平台均已支持。Windows 需 WebView2（Win11 内置，Win10 需安装）；macOS 已默认在 `tauri.conf.json` 启用 `macOSPrivateApi` 以实现透明无边框桌宠窗口（应用因此无法上架 Mac App Store）；Linux 需系统 WebKit2GTK（见第二步）。

### 第一步：克隆项目

```bash
git clone https://github.com/<your-org>/firefly-companion.git
cd firefly-companion
```

### 第二步：安装系统依赖

**通用依赖（三平台均需要）：**

1. **Node.js 20+**：从 [nodejs.org](https://nodejs.org/) 下载 LTS 版本
2. **pnpm**：`npm install -g pnpm`
3. **Rust**：从 [rustup.rs](https://rustup.rs/) 安装（**Windows 选 MSVC 工具链**；macOS / Linux 默认即可）
4. **Python 3.11+**：从 [python.org](https://www.python.org/downloads/) 下载（**Windows 安装时勾选 "Add Python to PATH"**；macOS / Linux 用系统包管理器或 `pyenv`）

**平台专属系统库：**

- **Windows**：安装 [Visual Studio Build Tools](https://visualstudio.microsoft.com/zh-hans/downloads/)，勾选"使用 C++ 的桌面开发"工作负载（提供 MSVC）；WebView2 运行时（Win11 内置）。
- **macOS**：安装 [Xcode Command Line Tools](https://developer.apple.com/xcode/)（`xcode-select --install`），提供 clang 与系统 WebKit。
- **Linux (Debian/Ubuntu)**：`sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev pkg-config build-essential`。其它发行版对应安装 `webkit2gtk` 与 `libappindicator` 开发包。

### 第三步：安装项目依赖

```bash
# 在项目根目录执行（Windows / macOS / Linux 通用，Windows 可使用 PowerShell 或 Git Bash）

# 1. 创建 Python 虚拟环境并激活
python -m venv .venv
#   Windows:     .\.venv\Scripts\Activate.ps1
#   macOS/Linux: source .venv/bin/activate

# 2. 安装 Python 后端依赖
pip install -e apps/server

# 3. 安装前端依赖
pnpm install
```

### 第四步：配置 LLM

1. 复制配置模板：
   ```bash
   # Windows (PowerShell)
   copy config\default.json config\default.local.json
   # macOS / Linux
   cp config/default.json config/default.local.json
   ```

2. 编辑 `config/default.local.json`，填入你的 LLM API Key（参考 `config/default.json` 了解完整选项）：
   ```json
   {
     "llm": {
       "provider": "openai_compat",
       "model": "你的模型名",
       "apiKey": "你的API Key",
       "baseUrl": "你的API端点"
     }
   }
   ```

   兼容的 Provider：OpenAI / Claude / DeepSeek / 智谱 GLM-4 / 通义千问等标准 OpenAI 兼容 API。

> **安全提醒**：`config/default.local.json` 已加入 `.gitignore`，不会被提交到 Git。请勿将 API Key 写入 `config/default.json`。

### 第五步：启动

```bash
pnpm dev
```

Tauri 桌面应用启动后会自动在后台拉起 Python 后端（Sidecar 模式），无需手动启动服务。

首次启动时 ONNX 引擎会自动从 HuggingFace 下载语义模型（约 120MB），后续启动复用缓存。

### 可选：语音环境

GPT-SoVITS 本地语音合成是**可选的**，不配置也能正常使用对话功能。

如需启用流萤原声 TTS：

**一键安装（推荐）：**
- **Windows**：双击 `resources/voice/gpt_sovits_engine/install_env.bat`
- **macOS / Linux**：在终端执行 `bash resources/voice/gpt_sovits_engine/install_env.sh`

两脚本都会自动创建独立 Python 环境（约 3-5 GB，10-20 分钟）。

**手动配置**：如已有 GPT-SoVITS 整合包，在 **设置 → 语音** 面板中填入解释器路径即可（Windows 为 `整合包/env/Scripts/python.exe`，macOS / Linux 为 `整合包/env/bin/python`）。

**推理加速说明：**
- **Windows / Linux（NVIDIA）**：自动启用 CUDA 推理，延迟从 CPU 的 30-60s 降至 ~0.3s。
- **macOS（Apple Silicon）**：通过 MPS 加速（实验性，GPT-SoVITS 官方未正式支持 macOS）。
- **无 GPU**：自动回退 CPU 推理，无需额外配置。

### 常见问题

<details>
<summary><b>Q: pnpm install 报错？</b></summary>

确保使用 pnpm 而非 npm/yarn：`npm install -g pnpm`，然后重试。
</details>

<details>
<summary><b>Q: Tauri 编译报错找不到工具链 / WebKit？</b></summary>

- **Windows**：安装 [Visual Studio Build Tools](https://visualstudio.microsoft.com/zh-hans/downloads/)，勾选"使用 C++ 的桌面开发"工作负载（提供 MSVC）。
- **macOS**：运行 `xcode-select --install` 安装命令行工具。
- **Linux**：安装 WebKit 开发库，例如 Debian/Ubuntu：`sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev pkg-config`。
</details>

<details>
<summary><b>Q: 启动后界面空白 / 连接不上后端？</b></summary>

检查 8765 端口是否被占用（Windows：`netstat -ano | findstr :8765`；macOS / Linux：`lsof -i :8765`），如有占用先结束进程。Tauri 会自动拉起 Python 后端，正常情况下等待几秒即可。
</details>

<details>
<summary><b>Q: ONNX 模型下载失败？</b></summary>

国内用户可能需要配置 HuggingFace 镜像：设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`。
</details>

<details>
<summary><b>Q: macOS / Linux 能用吗？</b></summary>

已经支持。自 0.2.x 起 `sidecar.rs`、`service_launcher.py`、`dev.mjs` 等进程管理与语音启动逻辑均已跨平台化，Tauri 打包目标也加入了 `app` / `dmg` / `deb` / `appimage`。

仍需注意的差异：
- **图标**：首次打包前运行 `pnpm --filter desktop tauri icon <源png>` 生成各平台图标（.icns / .png）。
- **macOS 透明桌宠**：已默认开启 `macOSPrivateApi`；透明无边框窗口可用，但应用无法通过 Mac App Store 分发。
- **语音 CUDA**：macOS 无 CUDA，使用 MPS / CPU；GPT-SoVITS 在 macOS 上为实验性支持。
</details>

---

## 📦 项目资源说明

### Live2D 模型

项目中包含的流萤 Live2D 模型文件位于 `resources/live2d/firefly/` 目录，模型来源于 B 站 UP 主 [**@是依七哒**](https://space.bilibili.com/457683484)。

> **版权声明**：流萤（Firefly）角色版权归 **米哈游 (miHoYo)** 所有。本项目代码部分采用 MIT 协议，角色形象与 Live2D 模型版权归属米哈游，仅供个人学习交流使用，禁止商用。

### 表情包与图片

`resources/memes/` 中的表情包与 `resources/avatar/`、`resources/photo/` 中的图片素材部分来源于网络收集，部分由 AI 生成，仅供个人学习交流使用。

### 语音模型

流萤 TTS 语音权重来源于 B 站 UP 主 [**星萤青焰灼**](https://space.bilibili.com/1831492534)，本项目不声明其版权。用户应自行确保合法使用。

### 剧情库与人物设计

剧情库与人物设计参考了以下开源项目，感谢原作者的无私贡献：

- [**HSRChat**](https://github.com/XCreeperPa/HSRChat) —— 提供《崩坏：星穹铁道》全量剧情 / 角色 / NPC 对话 wiki 数据
- [**firefly-skill**](https://github.com/HeartEase1/firefly-skill) —— 提供流萤角色人设、剧情库与相关技能定义参考

---

## 📂 项目结构

```
firefly-companion/
├── apps/
│   ├── desktop/             # Tauri 2 + Vue 3 前端
│   │   └── src/
│   │       ├── components/  # Live2DPet / ChatPanel / Settings / MemoryWidget ...
│   │       ├── stores/      # Pinia 状态管理
│   │       └── composables/ # 组合式函数
│   └── server/              # FastAPI 后端
│       └── app/
│           ├── api/         # REST + WebSocket 路由
│           ├── core/
│           │   ├── llm/         # LLM Provider 适配
│           │   ├── memory/      # 记忆系统（双引擎向量 + Mem0）
│           │   ├── concern/     # 双引擎主动关怀
│           │   ├── agent/       # Agent 任务执行
│           │   ├── voice/       # TTS / STT
│           │   ├── persona/     # 人设系统（builder.py 构建 prompt）
│           │   └── hsr_lore.py       # 角色知识检索引擎（FTS + ONNX 混合检索 + 防幻觉注入）
│           └── models/      # 数据模型
│       └── scripts/
│           └── build_lore_index.py   # 知识索引构建工具（一键重建 6717 块）
├── data/
│   ├── knowledge/
│   │   ├── firefly_lore.md        # 流萤亲历记忆
│   │   ├── *_lore.md              # 世界记忆 ×5（雅利洛/仙舟/翁法罗斯/二相乐园/空间站）
│   │   ├── facts.yaml              # 确定性事实表（73 条，声明式防幻觉）
│   │   └── curated_cards/         # 防幻觉精选卡片（29 张，触发正则强制注入）
│   ├── lore_index.db              # 知识检索索引（SQLite FTS5 + 向量）
│   └── onnx_model/                # 语义模型缓存
├── config/
│   ├── default.json         # 默认配置（完整选项）
│   ├── default.local.json   # 本地覆盖配置（API Key 等）
│   └── persona/
│       └── firefly.yaml     # 流萤人设（双模式语气 / 记忆规则 / 主动聊天模板）
├── resources/
│   ├── live2d/firefly/      # Live2D 模型文件
│   ├── memes/               # 表情包
│   ├── avatar/              # 头像
│   ├── photo/               # 背景图
│   ├── voice/               # TTS 引擎与模型
│   └── 流萤/                # 流萤角色文本素材（主线剧情 / 角色游戏 / 官方视频文本）
└── packages/
    └── shared-types/        # 前后端共享类型定义
```

---

## 📄 版权声明

- 流萤（Firefly）角色版权归 **米哈游 (miHoYo)** 所有
- 本项目角色形象、Live2D 模型版权归属米哈游；Live2D 模型来源于 B 站 UP 主 [**@是依七哒**](https://space.bilibili.com/457683484)
- TTS 语音权重来源于 B 站 UP 主 [**星萤青焰灼**](https://space.bilibili.com/1831492534)
- 剧情库与人物设计参考了 [HSRChat](https://github.com/XCreeperPa/HSRChat) 与 [firefly-skill](https://github.com/HeartEase1/firefly-skill) 两个开源项目
- 表情包与图片素材部分来源于网络，部分由 AI 生成
- TTS 语音权重由社区贡献者提供
- 本项目代码部分采用 **MIT** 协议
- 整体项目**仅供个人学习交流使用，禁止商用**
