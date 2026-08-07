import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# 音频缓存目录 (在数据根 data/audio_cache 下)
from app.core import paths as _paths

CACHE_DIR = _paths.AUDIO_CACHE_DIR
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# EdgeTTS 默认声音列表
SUPPORTED_EDGE_VOICES = [
    {
        "id": "zh-CN-XiaoyiNeural",
        "name": "晓伊 (Xiaoyi)",
        "gender": "Female",
        "description": "甜美活泼少女音，较契合流萤声线",
        "recommended": False,
    },
    {
        "id": "zh-CN-XiaoxiaoNeural",
        "name": "晓晓 (Xiaoxiao)",
        "gender": "Female",
        "description": "温暖亲切女生音",
        "recommended": False,
    },
    {
        "id": "zh-CN-YunxiNeural",
        "name": "云希 (Yunxi)",
        "gender": "Male",
        "description": "阳光少年音",
        "recommended": False,
    },
    {
        "id": "zh-CN-YunjianNeural",
        "name": "云健 (Yunjian)",
        "gender": "Male",
        "description": "沉稳硬朗男声",
        "recommended": False,
    },
]


# MiniMax 云端音色列表（可在设置中选择的音色 ID，实际效果取决于 clone 的 voice_id）
SUPPORTED_MINIMAX_VOICES: List[Dict[str, Any]] = [
    {
        "id": "male-qn-qingse",
        "name": "青涩青年音色 (Male-QN-Qingse)",
        "gender": "Male",
        "description": "MiniMax 官方青涩青年音色",
        "recommended": False,
    },
    {
        "id": "female-shaonv",
        "name": "少女音色 (Female-Shaonv)",
        "gender": "Female",
        "description": "MiniMax 官方少女音色",
        "recommended": True,
    },
    {
        "id": "female-yujie",
        "name": "御姐音色 (Female-Yujie)",
        "gender": "Female",
        "description": "MiniMax 官方御姐音色",
        "recommended": False,
    },
    {
        "id": "male-qn-jingying",
        "name": "精英青年音色 (Male-QN-Jingying)",
        "gender": "Male",
        "description": "MiniMax 官方精英青年音色",
        "recommended": False,
    },
    {
        "id": "presenter_male",
        "name": "男性主持人 (Presenter-Male)",
        "gender": "Male",
        "description": "MiniMax 官方男性主持人音色",
        "recommended": False,
    },
    {
        "id": "presenter_female",
        "name": "女性主持人 (Presenter-Female)",
        "gender": "Female",
        "description": "MiniMax 官方女性主持人音色",
        "recommended": False,
    },
    {
        "id": "custom",
        "name": "自定义声音克隆 (Custom Clone)",
        "gender": "Unknown",
        "description": "使用 MiniMax 声音克隆的 voice_id（在下方填入克隆 ID）",
        "recommended": False,
    },
]


class TTSService:
    """TTS 核心服务：支持 Edge-TTS、GPT-SoVITS 专属驱动、MiniMax 云端驱动，兼备离线缓存与自动降级；
    GPT-SoVITS 空闲 5 分钟自动关闭以释放内存，下次使用按需拉起。"""

    def __init__(self):
        self.default_edge_voice = "zh-CN-XiaoyiNeural"
        self.gpt_sovits_url = "http://127.0.0.1:9880"
        self.default_provider = "edge-tts"
        self.minimax_api_url = "https://api.minimaxi.com/v1/t2a_v2"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取持久 HTTP 客户端（复用连接池，避免每次请求重新 TCP 握手）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(180.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
                trust_env=False,
            )
        return self._client

    @staticmethod
    def get_resource_voice_dir() -> Path:
        """获取项目内置流萤语音模型资源目录 (resources/voice/firefly)"""
        return _paths.FIREFLY_VOICE_DIR

    def list_available_voices() -> List[Dict[str, Any]]:
        """获取支持的音色列表"""
        return SUPPORTED_EDGE_VOICES

    async def generate_speech_edge(
        self, text: str, voice_id: Optional[str] = None
    ) -> bytes:
        """通过 Edge-TTS 合成语音"""
        import edge_tts

        from app.config import get_settings
        live_settings = get_settings()
        voice = voice_id or live_settings.voice.tts.voice or self.default_edge_voice

        # 清理与截断文本
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("TTS 待合成文本不能为空")

        cache_key = hashlib.md5(f"edge-tts_{voice}_{clean_text}".encode("utf-8")).hexdigest()
        cache_file = CACHE_DIR / f"{cache_key}.mp3"
        if cache_file.exists():
            logger.info(f"[TTS Cache Hit] {cache_file.name}")
            return cache_file.read_bytes()

        logger.info(f"[Edge-TTS Generating] voice={voice}, text={clean_text[:20]}...")
        communicate = edge_tts.Communicate(clean_text, voice)
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])

        result_bytes = bytes(audio_data)
        if result_bytes:
            cache_file.write_bytes(result_bytes)
        return result_bytes

    async def generate_speech_minimax(
        self,
        text: str,
        voice_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> bytes:
        """通过 MiniMax 云端 TTS API 合成语音。需提供 apiKey 和 voice_id。"""
        from app.config import get_settings
        live_settings = get_settings()
        key = api_key or live_settings.voice.minimax.api_key
        vid = voice_id or live_settings.voice.minimax.voice_id

        if not key:
            raise ValueError("MiniMax API Key 未配置，请在设置中填写。")
        if not vid:
            raise ValueError("MiniMax Voice ID 未配置，请在设置中选择音色。")

        clean_text = text.strip()
        if not clean_text:
            raise ValueError("TTS 待合成文本不能为空")

        cache_key_hash = hashlib.md5(f"minimax_{vid}_{clean_text}".encode("utf-8")).hexdigest()
        cache_file = CACHE_DIR / f"{cache_key_hash}.mp3"
        if cache_file.exists():
            logger.info(f"[MiniMax Cache Hit] {cache_file.name}")
            return cache_file.read_bytes()

        # 构建请求 — 严格对齐 MiniMax 官方 T2A v2 文档
        payload = {
            "model": "speech-2.8-hd",
            "text": clean_text,
            "stream": False,
            "voice_setting": {
                "voice_id": vid,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
            "output_format": "hex",
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        logger.info(
            f"[MiniMax] POST {self.minimax_api_url} "
            f"model=speech-2.8-hd voice={vid} text={clean_text[:30]}"
        )

        client = await self._get_client()
        resp = await client.post(self.minimax_api_url, json=payload, headers=headers)

        # 诊断：记录原始响应
        logger.info(f"[MiniMax] HTTP {resp.status_code}, body={resp.text[:500]}")

        if resp.status_code != 200:
            error_text = resp.text[:500]
            logger.error(f"[MiniMax] API 返回 {resp.status_code}: {error_text}")
            raise RuntimeError(f"MiniMax API 返回状态 {resp.status_code}: {error_text}")

        import json as _json
        data = _json.loads(resp.text)

        # 检查 base_resp 状态（MiniMax 标准错误格式）
        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code") != 0:
            raise RuntimeError(
                f"MiniMax API 业务错误 (code={base_resp.get('status_code')}): "
                f"{base_resp.get('status_msg', 'unknown')}"
            )

        # 响应格式: {"data": {"audio": "<hex编码>", "status": 2}}
        audio_hex = data.get("data", {}).get("audio")
        if not audio_hex:
            # 也可能返回 URL
            audio_url = data.get("audio_file") or data.get("extra_info", {}).get("audio_file")
            if audio_url:
                resp2 = await client.get(audio_url)
                if resp2.status_code == 200:
                    result_bytes = resp2.content
                    cache_file.write_bytes(result_bytes)
                    return result_bytes
            raise RuntimeError(f"MiniMax 返回中未找到音频数据: {_json.dumps(data, ensure_ascii=False)[:300]}")

        # 官方默认返回 hex 编码，非 base64
        result_bytes = bytes.fromhex(audio_hex)
        if result_bytes:
            cache_file.write_bytes(result_bytes)
        return result_bytes

    async def generate_speech_gpt_sovits(
        self,
        text: str,
        api_url: Optional[str] = None,
        prompt_text: Optional[str] = None,
        prompt_language: str = "zh",
    ) -> bytes:
        """通过 GPT-SoVITS 本地/远程 API 生成流萤原声"""
        from app.config import get_settings
        live_settings = get_settings()
        url = (api_url or live_settings.voice.gpt_sovits.api_url or self.gpt_sovits_url).rstrip("/")
        voice = live_settings.voice.tts.voice or self.default_edge_voice
        clean_text = text.strip()

        cache_key = hashlib.md5(f"gpt-sovits_{voice}_{clean_text}".encode("utf-8")).hexdigest()
        cache_file = CACHE_DIR / f"{cache_key}.wav"
        if cache_file.exists():
            logger.info(f"[GPT-SoVITS Cache Hit] {cache_file.name}")
            # 缓存命中也标记活跃，避免空闲计时器误判为无使用
            from app.core.voice.service_launcher import mark_gpt_sovits_active
            mark_gpt_sovits_active()
            return cache_file.read_bytes()

        # 若目标是 127.0.0.1/localhost，自动确保本地 9880 端口服务已启动（每次快速端口检测，支持空闲后重新拉起）
        if "127.0.0.1" in url or "localhost" in url:
            from app.core.voice.service_launcher import is_port_in_use, ensure_gpt_sovits_started
            if not is_port_in_use("127.0.0.1", 9880):
                import asyncio
                started = await asyncio.to_thread(ensure_gpt_sovits_started)
                if not started:
                    raise RuntimeError(
                        "GPT-SoVITS 本地推理服务启动失败（模型加载超时或端口未连通）。"
                        "请检查 engine 日志：resources/voice/gpt_sovits_engine/logs/"
                    )

        # 检查是否有自动配套的参考音频
        ref_dir = self.get_resource_voice_dir() / "ref_audio"
        ref_audio_path = ""
        ref_text = prompt_text or "接下来，我们走这边吧。"

        if ref_dir.exists():
            index_json = ref_dir / "index.json"
            if index_json.exists():
                try:
                    import json
                    with open(index_json, "r", encoding="utf-8") as f:
                        items = json.load(f)
                    if items:
                        ref_audio_path = str((ref_dir / items[0]["file"]).resolve())
                        ref_text = prompt_text or items[0]["text"]
                except Exception as e:
                    logger.warning(f"[TTS] 读取 index.json 失败: {e}")

            if not ref_audio_path:
                wav_files = list(ref_dir.glob("*.wav"))
                if wav_files:
                    ref_audio_path = str(wav_files[0].resolve())

        # 构建标准的 API v2 请求 payload (支持 text_lang / text_language 兼容)
        payload = {
            "text": clean_text,
            "text_lang": "zh",
            "text_language": "zh",
            # 2026-08-07：整段一次合成（默认 cut5 按句切段再拼接，段间拼接会缺字）
            "text_split_method": "cut0",
            # 2026-08-07 音质调优：默认 temperature=1/top_k=5 偏随机（语气飘、偶发口胡）；
            # 0.8/3 稳定音色与停顿，减少段间风格跳跃（对速度影响可忽略）。
            "temperature": 0.8,
            "top_k": 3,
            "top_p": 0.95,
        }
        if ref_audio_path:
            payload["ref_audio_path"] = ref_audio_path
            payload["prompt_text"] = ref_text
            payload["prompt_lang"] = prompt_language
            payload["prompt_language"] = prompt_language

        logger.info(
            f"[GPT-SoVITS Requesting] url={url}, text={clean_text[:20]}..., "
            f"ref_audio={'YES' if ref_audio_path else 'MISSING'}"
        )

        # 使用持久 HTTP 客户端复用 TCP 连接，避免每次请求重新握手
        client = await self._get_client()
        response = await client.post(f"{url}/tts", json=payload)
        if response.status_code == 200 and response.content:
            cache_file.write_bytes(response.content)
            # 标记使用活跃，重置空闲计时器
            from app.core.voice.service_launcher import mark_gpt_sovits_active
            mark_gpt_sovits_active()
            return response.content

        # 若状态码非 200，抛出具体错误供上层日志记录或触发 Edge-TTS 降级
        raise RuntimeError(f"GPT-SoVITS API 返回状态 {response.status_code}: {response.text[:200]}")

    async def generate_speech(
        self,
        text: str,
        provider: str = "edge-tts",
        voice_id: Optional[str] = None,
        gpt_sovits_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> bytes:
        """统一合成接口：按 provider 分发至 Edge-TTS / GPT-SoVITS / MiniMax；
        若指定 MiniMax 且云端故障，自动降级到 Edge-TTS。"""
        clean_text = text.strip()
        if not clean_text:
            return b""

        prov = (provider or "edge-tts").lower()

        # MiniMax 云端 TTS
        if "minimax" in prov:
            logger.info("[TTS] Using MiniMax Cloud Provider...")
            return await self.generate_speech_minimax(
                text=clean_text, voice_id=voice_id, api_key=api_key,
            )

        # GPT-SoVITS 本地驱动
        elif "gpt" in prov or "sovits" in prov:
            logger.info("[TTS] Using GPT-SoVITS Firefly Model Provider...")
            return await self.generate_speech_gpt_sovits(
                text=clean_text, api_url=gpt_sovits_url
            )

        # 默认使用 Edge-TTS
        return await self.generate_speech_edge(text=clean_text, voice_id=voice_id)


# ── 音频缓存管理 ──

# 默认清理策略
DEFAULT_TTL_DAYS = 7        # 超过 7 天未使用的文件自动清理
MAX_CACHE_SIZE_MB = 200     # 缓存总容量上限（MB）
CLEANUP_TARGET_RATIO = 0.75 # 超出上限时清理到 75%


def get_audio_cache_stats() -> dict:
    """返回音频缓存统计信息：文件数、总大小、最旧/最新文件时间。"""
    if not CACHE_DIR.exists():
        return {"file_count": 0, "total_size_mb": 0.0, "oldest_ts": None, "newest_ts": None}
    files = list(CACHE_DIR.iterdir())
    if not files:
        return {"file_count": 0, "total_size_mb": 0.0, "oldest_ts": None, "newest_ts": None}
    total_bytes = 0
    mtimes = []
    for f in files:
        if f.is_file():
            try:
                total_bytes += f.stat().st_size
                mtimes.append(f.stat().st_mtime)
            except OSError:
                pass
    return {
        "file_count": len(files),
        "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        "oldest_ts": min(mtimes) if mtimes else None,
        "newest_ts": max(mtimes) if mtimes else None,
    }


def cleanup_audio_cache(
    ttl_days: int | None = None,
    max_size_mb: int | None = None,
    force: bool = False,
) -> dict:
    """清理音频缓存。

    策略：
      - force=True：无条件删除全部文件
      - 否则 1) 先删超过 ttl_days 的文件；2) 若总大小仍超 max_size_mb，按修改时间从旧到新删至 75%。

    Returns:
        {"deleted_count": N, "freed_mb": M, "remaining_mb": R}
    """
    if not CACHE_DIR.exists():
        return {"deleted_count": 0, "freed_mb": 0.0, "remaining_mb": 0.0}

    # 收集文件信息
    files_info: list[tuple[Path, float, int]] = []
    for f in CACHE_DIR.iterdir():
        if f.is_file():
            try:
                st = f.stat()
                files_info.append((f, st.st_mtime, st.st_size))
            except OSError:
                pass

    if not files_info:
        return {"deleted_count": 0, "freed_mb": 0.0, "remaining_mb": 0.0}

    deleted_count = 0
    freed_bytes = 0
    current_total = sum(fi[2] for fi in files_info)

    if force:
        # 强制清理：直接删除所有文件
        for filepath, _mtime, fsize in files_info:
            try:
                filepath.unlink()
                deleted_count += 1
                freed_bytes += fsize
                logger.info(f"[AudioCache] 强制清理: {filepath.name}")
            except OSError:
                logger.warning(f"[AudioCache] 无法删除: {filepath.name}")
        remaining_mb = 0.0
        logger.info(
            f"[AudioCache] 强制清理完成: 删除 {deleted_count} 个文件, "
            f"释放 {freed_bytes / (1024*1024):.1f} MB"
        )
        return {
            "deleted_count": deleted_count,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
            "remaining_mb": remaining_mb,
        }

    ttl_days = ttl_days if ttl_days is not None else DEFAULT_TTL_DAYS
    max_size_mb = max_size_mb if max_size_mb is not None else MAX_CACHE_SIZE_MB
    now = time.time()
    ttl_seconds = ttl_days * 86400

    # 按修改时间从旧到新排序
    files_info.sort(key=lambda x: x[1])

    # 阶段1：TTL 过期清理
    remaining = []
    for filepath, mtime, fsize in files_info:
        if (now - mtime) > ttl_seconds:
            try:
                filepath.unlink()
                deleted_count += 1
                freed_bytes += fsize
                current_total -= fsize
                logger.info(f"[AudioCache] TTL 清理: {filepath.name}")
            except OSError:
                remaining.append((filepath, mtime, fsize))
                current_total -= fsize  # 删除失败也移除计数
                logger.warning(f"[AudioCache] 无法删除: {filepath.name}")
        else:
            remaining.append((filepath, mtime, fsize))

    # 阶段2：容量上限清理（删到 target_ratio 以下）
    max_bytes = max_size_mb * 1024 * 1024
    target_bytes = int(max_bytes * CLEANUP_TARGET_RATIO)
    if current_total > max_bytes:
        remaining.sort(key=lambda x: x[1])  # 确保最旧的在前
        for filepath, _mtime, fsize in remaining:
            if current_total <= target_bytes:
                break
            try:
                filepath.unlink()
                deleted_count += 1
                freed_bytes += fsize
                current_total -= fsize
                logger.info(f"[AudioCache] 容量清理: {filepath.name}")
            except OSError:
                logger.warning(f"[AudioCache] 无法删除: {filepath.name}")

    remaining_mb = round(current_total / (1024 * 1024), 2)
    logger.info(
        f"[AudioCache] 清理完成: 删除 {deleted_count} 个文件, "
        f"释放 {freed_bytes / (1024*1024):.1f} MB, 剩余 {remaining_mb} MB"
    )
    return {
        "deleted_count": deleted_count,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "remaining_mb": remaining_mb,
    }


# ── 全局单例 ──

_tts_service_instance: Optional[TTSService] = None


def get_tts_service() -> TTSService:
    global _tts_service_instance
    if _tts_service_instance is None:
        _tts_service_instance = TTSService()
    return _tts_service_instance
