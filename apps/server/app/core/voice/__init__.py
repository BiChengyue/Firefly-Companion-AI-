"""
Voice 模块包 - 提供 TTS 语音合成与 GPT-SoVITS / Edge-TTS 双驱动管理
"""

from app.core.voice.tts import TTSService, get_tts_service

__all__ = ["get_tts_service", "TTSService"]
