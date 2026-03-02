"""
STT (Speech-to-Text) API 端点

提供:
  - POST /stt/transcribe         — 上传音频文件, 返回转写文本
  - GET  /stt/stream             — 实时音频 SSE 流式转写
  - GET  /stt/models             — 可用 STT 模型列表
  - GET  /stt/status             — STT 配置状态
"""
import asyncio
import json
import logging
import time
import threading
from typing import Optional

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/studio-api/stt", tags=["STT"])


@router.get("/status")
async def stt_status():
    """获取 STT 服务配置状态"""
    from backend.services.stt_service import get_stt_provider_info
    return await get_stt_provider_info()


@router.get("/models")
async def stt_models():
    """列出可用的 STT 模型"""
    from backend.services.stt_service import list_stt_models
    return await list_stt_models()


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(..., description="音频文件 (WAV/WebM/MP3/OGG 等)"),
    model: Optional[str] = Form(None, description="STT 模型 ID (留空用全局默认)"),
    language: Optional[str] = Form(None, description="语言提示 (如 zh, en, ja)"),
    prompt: Optional[str] = Form(None, description="上下文提示词"),
):
    """非流式转写 — 上传音频文件, 返回完整文本"""
    from backend.services.stt_service import transcribe_file

    audio_data = await file.read()
    if not audio_data:
        return {"error": "空文件", "text": ""}

    filename = file.filename or "audio.wav"
    logger.info(f"STT 转写请求: {filename} ({len(audio_data)} bytes), model={model}, lang={language}")

    try:
        result = await transcribe_file(
            audio_data=audio_data,
            filename=filename,
            model=model,
            language=language,
            prompt=prompt,
        )
        return result
    except ValueError as e:
        # 配置错误
        return {"error": str(e), "text": ""}
    except RuntimeError as e:
        # API 调用错误
        return {"error": str(e), "text": ""}
    except Exception as e:
        logger.exception(f"STT 转写异常: {e}")
        return {"error": f"转写失败: {e}", "text": ""}


@router.get("/transcribe-stream")
async def transcribe_stream_endpoint(
    device: Optional[int] = Query(None, description="服务端音频设备 ID"),
    model: Optional[str] = Query(None, description="STT 模型 ID"),
    language: Optional[str] = Query(None, description="语言提示"),
    samplerate: int = Query(16000, description="采样率"),
    channels: int = Query(1, description="声道数"),
    duration: int = Query(30, description="最大录音时长 (秒)"),
):
    """流式转写 — 从服务端麦克风实时录音并逐句返回

    返回 SSE 事件流:
      data: {"type": "recording", "message": "开始录音"}
      data: {"type": "partial", "text": "...", "accumulated": "...", "is_final": false}
      data: {"type": "final", "text": "完整文本", "is_final": true}
      data: {"type": "error", "error": "..."}
    """
    try:
        import sounddevice as sd
        import numpy as np
    except ImportError:
        async def _error_gen():
            yield f"data: {json.dumps({'type': 'error', 'error': 'sounddevice 未安装'})}\n\n"
        return StreamingResponse(_error_gen(), media_type="text/event-stream")

    from backend.services.stt_service import transcribe_stream

    stop_event = threading.Event()

    async def _audio_generator():
        """从麦克风捕获音频, 产出 PCM 数据块"""
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=100)

        def _callback(indata, frames, time_info, status):
            if stop_event.is_set():
                raise sd.CallbackAbort()
            pcm = indata.copy().tobytes()
            try:
                loop.call_soon_threadsafe(queue.put_nowait, pcm)
            except asyncio.QueueFull:
                pass

        try:
            stream = sd.InputStream(
                device=device,
                samplerate=samplerate,
                channels=channels,
                dtype="int16",
                blocksize=int(samplerate * 0.1),  # 100ms 块
                callback=_callback,
            )
            stream.start()

            start_time = time.monotonic()
            while not stop_event.is_set():
                if time.monotonic() - start_time > duration:
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=0.5)
                    if data is not None:
                        yield data
                except asyncio.TimeoutError:
                    continue

        except Exception as e:
            logger.error(f"音频捕获错误: {e}")
        finally:
            stop_event.set()
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    async def _sse_generator():
        yield f"data: {json.dumps({'type': 'recording', 'message': '开始录音'})}\n\n"

        try:
            async for event in transcribe_stream(
                audio_iterator=_audio_generator(),
                model=model,
                language=language,
                sample_rate=samplerate,
                channels=channels,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception(f"流式转写异常: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
            stop_event.set()
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
