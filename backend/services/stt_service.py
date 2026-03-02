"""
STT (Speech-to-Text) 服务层

支持多种后端:
  1. OpenAI-compatible API (Whisper / Groq / 本地 faster-whisper-server 等)
  2. 未来可扩展: Azure Speech, Google STT 等

提供:
  - transcribe_file(): 非流式 — 上传音频文件 → 返回完整文本
  - transcribe_stream(): 流式 — 实时音频流 → SSE 逐句返回
"""
import asyncio
import io
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── 默认配置 ──
DEFAULT_STT_MODEL = "whisper-1"
DEFAULT_STT_API_BASE = ""  # 空 = 未配置


async def _get_stt_config() -> Dict[str, Any]:
    """从 DB 加载 STT 配置"""
    from backend.services.config_service import get_stt_config
    return await get_stt_config()


async def get_stt_provider_info() -> Dict[str, Any]:
    """获取 STT 提供商信息 (供前端展示)"""
    cfg = await _get_stt_config()
    provider = cfg.get("stt_provider", "") or "openai_compat"
    api_base = cfg.get("stt_api_base", "")
    model = cfg.get("stt_default_model", "") or DEFAULT_STT_MODEL
    api_key_configured = bool(cfg.get("stt_api_key", ""))
    allowlist = cfg.get("stt_model_allowlist", [])

    return {
        "provider": provider,
        "api_base": api_base,
        "default_model": model,
        "api_key_configured": api_key_configured,
        "model_allowlist": allowlist,
        "configured": bool(api_base),
    }


async def list_stt_models() -> List[Dict[str, str]]:
    """列出可用的 STT 模型

    如果有白名单, 返回白名单; 否则返回默认模型。
    未来可以从 provider 动态获取。
    """
    cfg = await _get_stt_config()
    allowlist = cfg.get("stt_model_allowlist", [])
    default_model = cfg.get("stt_default_model", "") or DEFAULT_STT_MODEL

    if allowlist:
        models = [{"id": m, "name": m} for m in allowlist]
    else:
        # 常见 STT 模型
        models = [
            {"id": "whisper-1", "name": "Whisper v1 (OpenAI)"},
            {"id": "whisper-large-v3", "name": "Whisper Large v3"},
            {"id": "whisper-large-v3-turbo", "name": "Whisper Large v3 Turbo"},
        ]

    # 确保 default_model 在列表中
    model_ids = {m["id"] for m in models}
    if default_model and default_model not in model_ids:
        models.insert(0, {"id": default_model, "name": default_model})

    return models


async def transcribe_file(
    audio_data: bytes,
    filename: str = "audio.wav",
    model: Optional[str] = None,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """非流式转写 — 上传音频文件, 返回完整文本

    使用 OpenAI-compatible /v1/audio/transcriptions 接口

    Args:
        audio_data: WAV / WebM / MP3 等音频二进制
        filename: 文件名 (用于 content-type 推断)
        model: 转写模型 ID (None = 使用全局默认)
        language: 语言提示 (如 "zh", "en")
        prompt: 上下文提示词

    Returns:
        {"text": "转写结果", "model": "...", "duration_ms": ..., ...}
    """
    cfg = await _get_stt_config()
    api_base = (cfg.get("stt_api_base", "") or "").rstrip("/")
    api_key = cfg.get("stt_api_key", "")
    default_model = cfg.get("stt_default_model", "") or DEFAULT_STT_MODEL
    use_model = model or default_model

    if not api_base:
        raise ValueError("STT 未配置: 请在设置中配置 STT API 地址 (stt_api_base)")

    url = f"{api_base}/v1/audio/transcriptions"
    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # multipart form
    files = {"file": (filename, audio_data, _guess_mime(filename))}
    data: Dict[str, str] = {"model": use_model}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    data["response_format"] = "verbose_json"

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, headers=headers, files=files, data=data)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if resp.status_code != 200:
        error_text = resp.text[:500]
        logger.error(f"STT API 错误 ({resp.status_code}): {error_text}")
        raise RuntimeError(f"STT API 返回 {resp.status_code}: {error_text}")

    try:
        result = resp.json()
    except Exception:
        # 可能返回纯文本
        result = {"text": resp.text.strip()}

    return {
        "text": result.get("text", ""),
        "model": use_model,
        "language": result.get("language", language or ""),
        "duration": result.get("duration"),
        "segments": result.get("segments"),
        "duration_ms": elapsed_ms,
    }


async def transcribe_stream(
    audio_iterator: AsyncGenerator[bytes, None],
    model: Optional[str] = None,
    language: Optional[str] = None,
    sample_rate: int = 16000,
    channels: int = 1,
) -> AsyncGenerator[Dict[str, Any], None]:
    """流式转写 — 从实时音频流逐句返回结果

    这里采用分块策略: 累积一定时长的音频后发送一次转写请求。
    对于真正的流式 STT (如 WebSocket 协议), 未来可扩展。

    当前实现: 分块累积 → 非流式 API 调用 → yield 结果

    Args:
        audio_iterator: 异步迭代器, 产出 PCM 音频块 (16-bit signed LE)
        model: 模型 ID
        language: 语言
        sample_rate: 采样率
        channels: 声道数

    Yields:
        {"type": "partial", "text": "...", "is_final": False}
        {"type": "final", "text": "...", "is_final": True}
    """
    import struct
    import wave

    cfg = await _get_stt_config()
    use_model = model or cfg.get("stt_default_model", "") or DEFAULT_STT_MODEL

    # 累积缓冲区
    buffer = bytearray()
    # 每 3 秒发送一次 (3 * sample_rate * channels * 2 bytes/sample)
    chunk_bytes = 3 * sample_rate * channels * 2
    total_text_parts: List[str] = []

    async for chunk in audio_iterator:
        buffer.extend(chunk)

        if len(buffer) >= chunk_bytes:
            # 转成 WAV
            wav_data = _pcm_to_wav(bytes(buffer), sample_rate, channels)
            buffer.clear()

            try:
                result = await transcribe_file(
                    audio_data=wav_data,
                    filename="chunk.wav",
                    model=use_model,
                    language=language,
                )
                text = result.get("text", "").strip()
                if text:
                    total_text_parts.append(text)
                    yield {
                        "type": "partial",
                        "text": text,
                        "accumulated": " ".join(total_text_parts),
                        "is_final": False,
                    }
            except Exception as e:
                logger.warning(f"流式转写分块失败: {e}")
                yield {"type": "error", "error": str(e), "is_final": False}

    # 处理剩余缓冲
    if buffer:
        wav_data = _pcm_to_wav(bytes(buffer), sample_rate, channels)
        try:
            result = await transcribe_file(
                audio_data=wav_data,
                filename="final_chunk.wav",
                model=use_model,
                language=language,
            )
            text = result.get("text", "").strip()
            if text:
                total_text_parts.append(text)
        except Exception as e:
            logger.warning(f"流式转写尾块失败: {e}")

    yield {
        "type": "final",
        "text": " ".join(total_text_parts),
        "is_final": True,
    }


# ── 辅助函数 ──

def _guess_mime(filename: str) -> str:
    """根据文件名推断 MIME 类型"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_map = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
        "m4a": "audio/mp4",
    }
    return mime_map.get(ext, "audio/wav")


def _pcm_to_wav(pcm_data: bytes, sample_rate: int, channels: int, sample_width: int = 2) -> bytes:
    """将 raw PCM 数据包装为 WAV 格式"""
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()
