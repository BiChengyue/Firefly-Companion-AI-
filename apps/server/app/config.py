"""配置管理：从 config/default.json 加载运行时配置。
对应 requirements_spec.md 3.5 / 3.8 / 3.9 / 3.10 节。
"""
import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    """LLM 提供商配置，支持 JSON camelCase 字段（api_key ↔ apiKey 等）。"""

    model_config = ConfigDict(populate_by_name=True)

    provider: str = "openai_compat"
    model: str = Field(default="deepseek-chat", alias="model")
    api_key: str = Field(default="", alias="apiKey")
    base_url: str = Field(default="https://api.deepseek.com/v1", alias="baseUrl")
    temperature: float = 0.8
    max_tokens: int = Field(default=2048, alias="maxTokens")
    enable_thinking: bool = Field(default=True, alias="enableThinking")


class ModeConfig(BaseModel):
    """模式切换配置 — 对应 spec 3.10"""

    model_config = ConfigDict(populate_by_name=True)

    current: str = "daily"  # "daily" | "work"
    switch_cooldown_ms: int = Field(default=500, alias="switchCooldownMs")


class STTConfig(BaseModel):
    engine: str = "faster-whisper"
    model: str = "base"
    language: str = "zh"


class TTSConfig(BaseModel):
    engine: str = "edge-tts"
    voice: str = "zh-CN-XiaoyiNeural"
    rate: str = "+0%"
    volume: str = "+0%"


class GPTSovitsConfig(BaseModel):
    """GPT-SoVITS 本地推理引擎配置 — pythonPath 指向整合包 env/python.exe。"""
    model_config = ConfigDict(populate_by_name=True)
    python_path: str = Field(default="", alias="pythonPath")
    api_url: str = Field(default="http://127.0.0.1:9880", alias="apiUrl")


class MiniMaxConfig(BaseModel):
    """MiniMax 云端 TTS 配置 — apiKey + voice_id（克隆后获得）。"""
    model_config = ConfigDict(populate_by_name=True)
    api_key: str = Field(default="", alias="apiKey")
    voice_id: str = Field(default="", alias="voiceId")


class VoiceConfig(BaseModel):
    stt: STTConfig = STTConfig()
    tts: TTSConfig = TTSConfig()
    gpt_sovits: GPTSovitsConfig = Field(default_factory=GPTSovitsConfig, alias="gptSovits")
    minimax: MiniMaxConfig = MiniMaxConfig()
    wake_word: str = "流萤"
    wake_word_enabled: bool = False


class MemoryConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    short_term_window: int = Field(default=20, alias="shortTermWindow")
    long_term_enabled: bool = Field(default=True, alias="longTermEnabled")
    chroma_path: str = Field(default="./data/chroma", alias="chromaPath")
    db_path: str = Field(default="./data/app.db", alias="dbPath")
    summary_threshold: int = Field(default=30, alias="summaryThreshold")
    confidence_threshold: float = Field(default=0.65, alias="confidenceThreshold")  # 统一置信度门槛
    memory_extraction_interval: int = Field(default=12, alias="memoryExtractionInterval")  # 每 N 轮对话触发一次记忆抽取
    decay_factor: float = Field(default=0.95, alias="decayFactor")  # 时间衰减因子
    decay_threshold: float = Field(default=0.30, alias="decayThreshold")  # 有效置信度低于此值的记忆不再注入 Prompt
    embedding_engine: str = Field(default="hash", alias="embeddingEngine")  # hash / onnx
    onnx_model_path: str = Field(default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", alias="onnxModelPath")


class Live2DConfig(BaseModel):
    model_path: str = "./resources/live2d/firefly/cat.model3.json"
    fps: int = 60
    scale: float = 1.0
    auto_blink: bool = True
    auto_breath: bool = True


class DesktopConfig(BaseModel):
    always_on_top: bool = True
    transparent: bool = True
    auto_start: bool = False
    global_shortcut: str = "Ctrl+Shift+F"
    ctrl_override_enabled: bool = True


class PerformanceConfig(BaseModel):
    """性能与质量基线 — 对应 spec 3.8"""
    llm_first_token_ms: int = 2000
    streaming_tps: int = 15
    memory_idle_mb: int = 350
    memory_active_mb: int = 600
    cpu_idle_percent: int = 3
    live2d_fps: int = 45
    startup_ms: int = 5000


class SandboxConfig(BaseModel):
    """Agent 安全沙箱配置 — 对应 spec 3.9"""
    model_config = ConfigDict(populate_by_name=True)
    allowed_paths: list[str] = Field(default_factory=lambda: ["~/Desktop", "~/Documents", "~/Downloads", "~/Pictures"], alias="allowedPaths")
    blocked_paths: list[str] = Field(default_factory=list, alias="blockedPaths")
    blocked_commands: list[str] = Field(default_factory=list, alias="blockedCommands")
    command_whitelist: list[str] = Field(default_factory=list, alias="commandWhitelist")
    require_approval: list[str] = Field(default_factory=list, alias="requireApproval")
    auto_approve: list[str] = Field(default_factory=list, alias="autoApprove")


class CodeRunnerConfig(BaseModel):
    """代码执行沙箱 — 对应 spec 3.9.2"""
    engine: str = "restrictedpython+subprocess"
    cpu_timeout: int = 10
    memory_limit_mb: int = 256
    network_access: bool = False
    output_truncate_kb: int = 10
    import_whitelist: list[str] = Field(default_factory=list)
    import_blacklist: list[str] = Field(default_factory=list)


class AgentToolsConfig(BaseModel):
    browser: dict = Field(default_factory=lambda: {"enabled": True, "headless": False})
    gui: dict = Field(default_factory=lambda: {"enabled": False})
    code_runner: dict = Field(default_factory=lambda: {"enabled": True, "timeout": 10})


class RollbackConfig(BaseModel):
    """任务回滚配置 — 对应 spec 3.9.4"""
    enabled: bool = True
    log_path: str = "./data/agent/rollback"


class AgentConfig(BaseModel):
    enabled: bool = True
    max_steps: int = 20
    hard_max_steps: int = 50
    step_timeout: int = 30
    sandbox: SandboxConfig = SandboxConfig()
    code_runner: CodeRunnerConfig = CodeRunnerConfig()
    tools: AgentToolsConfig = AgentToolsConfig()
    rollback: RollbackConfig = RollbackConfig()
    # ── 23.1.2 新增字段 ──
    tool_retry_count: int = 2          # 低风险工具失败后最大重试次数
    tool_retry_delay: float = 1.0      # 重试间隔（秒）
    compact_enabled: bool = True       # 是否启用上下文压缩
    compact_trigger_ratio: float = 0.75  # Token 用量到达模型窗口 75% 时触发压缩
    log_enabled: bool = True           # 是否启用结构化执行日志
    log_path: str = "./data/agent/logs"  # 执行日志路径
    checkpoint_enabled: bool = True    # 是否启用任务断点恢复


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class LoreConfig(BaseModel):
    """剧情知识库检索配置 — 第二十八阶段方案 B（离线索引 + 混合检索 + 置信度闸门）。"""
    model_config = ConfigDict(populate_by_name=True)
    enabled: bool = True
    index_path: str = Field(default="./data/lore_index.db", alias="indexPath")
    high_threshold: float = Field(default=0.75, alias="highThreshold")  # 纯向量分开高置信的门槛（实测噪声底 ~0.6）
    low_threshold: float = Field(default=0.60, alias="lowThreshold")    # 边缘候选门槛
    top_k: int = Field(default=4, alias="topK")
    max_chars: int = Field(default=2400, alias="maxChars")


class ProactiveChatConfig(BaseModel):
    """主动聊天（引擎 B）配置 — 前端 Settings 面板控制。"""
    model_config = ConfigDict(populate_by_name=True)
    enabled: bool = True
    idle_minutes: int = Field(default=45, alias="idleMinutes")
    quiet_hours_start: int = Field(default=23, alias="quietHoursStart")
    quiet_hours_end: int = Field(default=8, alias="quietHoursEnd")
    daily_limit: int = Field(default=5, alias="dailyLimit")


class SearchConfig(BaseModel):
    """搜索引擎与抓取配置。"""
    model_config = ConfigDict(populate_by_name=True)
    provider: str = Field(default="auto", alias="provider")  # auto / baidu / jina / tavily / bocha
    tavily_api_key: str = Field(default="", alias="tavilyApiKey")
    bocha_api_key: str = Field(default="", alias="bochaApiKey")
    max_results: int = Field(default=5, alias="maxResults")
    cache_ttl_hours: int = Field(default=24, alias="cacheTtlHours")


class Settings(BaseModel):
    llm: LLMConfig = LLMConfig()
    mode: ModeConfig = ModeConfig()
    voice: VoiceConfig = VoiceConfig()
    memory: MemoryConfig = MemoryConfig()
    live2d: Live2DConfig = Live2DConfig()
    desktop: DesktopConfig = DesktopConfig()
    performance: PerformanceConfig = PerformanceConfig()
    agent: AgentConfig = AgentConfig()
    server: ServerConfig = ServerConfig()
    lore: LoreConfig = LoreConfig()
    proactive_chat: ProactiveChatConfig = Field(default_factory=ProactiveChatConfig, alias="proactiveChat")
    search: SearchConfig = Field(default_factory=SearchConfig, alias="search")


def _deep_merge(base: dict, override: dict) -> None:
    """递归深合并 override 到 base（用于测试 profile 叠加）。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


@lru_cache
def get_settings() -> Settings:
    base_dir = Path(__file__).resolve().parents[3] / "config"
    data: dict = {}
    default_path = base_dir / "default.json"
    if default_path.exists():
        try:
            data.update(json.loads(default_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    local_path = base_dir / "default.local.json"
    if local_path.exists():
        try:
            local_data = json.loads(local_path.read_text(encoding="utf-8"))
            for k, v in local_data.items():
                if isinstance(v, dict) and k in data and isinstance(data[k], dict):
                    data[k].update(v)
                else:
                    data[k] = v
        except Exception:
            pass

    # ── 测试 Profile：FIREFLY_ENV=test 时叠加 config/test.json（不修改默认行为）──
    if os.getenv("FIREFLY_ENV") == "test":
        test_path = base_dir / "test.json"
        if test_path.exists():
            try:
                _deep_merge(data, json.loads(test_path.read_text(encoding="utf-8")))
            except Exception:
                pass

    # ── API Key 环境变量回退：default.json 中值为 "env" 时从环境变量读取 ──
    _llm = data.setdefault("llm", {})
    if _llm.get("apiKey", "") in ("", "env"):
        _llm["apiKey"] = os.getenv("AGNES_API_KEY", os.getenv("LLM_API_KEY", ""))
    _voice = data.setdefault("voice", {})
    _minimax = _voice.setdefault("minimax", {})
    if _minimax.get("apiKey", "") in ("", "env"):
        _minimax["apiKey"] = os.getenv("MINIMAX_API_KEY", "")

    return Settings(**data)
