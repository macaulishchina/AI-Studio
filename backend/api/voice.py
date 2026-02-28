"""
服务端语音硬件 API
- 设备枚举 (麦克风列表)
- 驱动 / 后端信息
- 实时音量 SSE 流
- 短时录音测试
"""
import asyncio
import json
import logging
import platform
import shutil
import subprocess
import threading
import time
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/studio-api/voice", tags=["voice"])

# ── 活跃 SSE 流追踪 (用于优雅关闭) ──
_active_stream_stops: list[threading.Event] = []


def shutdown_all_streams():
    """通知所有活跃的音频 SSE 流停止, 供 app shutdown 调用."""
    for evt in _active_stream_stops:
        evt.set()
    _active_stream_stops.clear()

# ── 可选依赖: sounddevice + numpy ──
_sd = None
_np = None
_sd_import_error: Optional[str] = None

try:
    import sounddevice as sd
    import numpy as np
    _sd = sd
    _np = np
except ImportError as e:
    _sd_import_error = str(e)
    logger.warning("sounddevice / numpy 未安装, 服务端音频功能不可用: %s", e)
except Exception as e:
    _sd_import_error = str(e)
    logger.warning("sounddevice 初始化失败 (可能缺少 PortAudio): %s", e)


def _sd_available() -> bool:
    return _sd is not None and _np is not None


# ───────────────────────── 1. 设备列表 ─────────────────────────

@router.get("/devices")
async def list_devices():
    """列出服务端所有音频输入设备"""
    if not _sd_available():
        return {
            "available": False,
            "error": _sd_import_error or "sounddevice not installed",
            "devices": [],
        }

    try:
        devices = _sd.query_devices()
        host_apis = _sd.query_hostapis()

        input_devices = []
        for idx, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                host_api = host_apis[dev["hostapi"]]
                input_devices.append({
                    "index": idx,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "default_samplerate": dev["default_samplerate"],
                    "host_api": host_api["name"],
                    "is_default": idx == _sd.default.device[0],
                })

        return {
            "available": True,
            "devices": input_devices,
            "default_device": _sd.default.device[0],
        }
    except Exception as e:
        logger.error("枚举音频设备失败: %s", e)
        return {"available": False, "error": str(e), "devices": []}


# ───────────────────────── 2. 驱动 / 系统信息 ─────────────────────────

@router.get("/driver-info")
async def driver_info():
    """返回服务端音频驱动 & 系统环境信息"""
    info: dict = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_machine": platform.machine(),
        "sounddevice_available": _sd_available(),
        "sounddevice_error": _sd_import_error,
        "portaudio": None,
        "host_apis": [],
        "alsa": None,
        "pulseaudio": None,
        "pipewire": None,
    }

    # sounddevice / PortAudio 信息
    if _sd_available():
        try:
            pa = _sd.query_hostapis()
            info["host_apis"] = [
                {"name": h["name"], "devices": h["devices"], "default_input": h["default_input_device"]}
                for h in pa
            ]
            # PortAudio 版本 (如可用)
            try:
                import _sounddevice as _pa_internal
                info["portaudio"] = getattr(_pa_internal, "pa_version_text", None)
            except Exception:
                info["portaudio"] = "unknown"
        except Exception as e:
            info["portaudio_error"] = str(e)

    # Linux 特有: ALSA / PulseAudio / PipeWire 检测
    if platform.system() == "Linux":
        info["alsa"] = _check_linux_tool("arecord", ["arecord", "--version"])
        info["pulseaudio"] = _check_linux_tool("pactl", ["pactl", "info"])
        info["pipewire"] = _check_linux_tool("pw-cli", ["pw-cli", "info", "0"])

        # ALSA 设备列表
        if shutil.which("arecord"):
            try:
                result = subprocess.run(
                    ["arecord", "-l"], capture_output=True, text=True, timeout=5
                )
                info["alsa_devices"] = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
            except Exception:
                info["alsa_devices"] = None

    return info


def _check_linux_tool(name: str, cmd: list[str]) -> dict | None:
    """检测 Linux 音频工具是否存在 + 简要信息"""
    path = shutil.which(name)
    if not path:
        return {"installed": False}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return {
            "installed": True,
            "path": path,
            "info": result.stdout[:500].strip() if result.returncode == 0 else result.stderr[:500].strip(),
        }
    except Exception as e:
        return {"installed": True, "path": path, "error": str(e)}


# ───────────────────────── 3. 实时音量 SSE 流 ─────────────────────────

@router.get("/level-stream")
async def level_stream(
    device: Optional[int] = Query(None, description="设备索引 (None=默认)"),
    samplerate: int = Query(16000, description="采样率"),
    interval_ms: int = Query(100, description="采样间隔 (ms)"),
    channels: int = Query(1, description="声道数"),
):
    """
    SSE 流: 持续推送服务端麦克风音量电平 (RMS + peak).
    前端可用此绘制实时音量条.
    """
    if not _sd_available():
        async def err_gen():
            yield f"data: {json.dumps({'error': 'sounddevice not available'})}\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    stop_event = threading.Event()
    _active_stream_stops.append(stop_event)

    async def _stream():
        """SSE 事件生成器: 在线程中采集音频, 异步推送电平"""
        loop = asyncio.get_event_loop()
        block_size = int(samplerate * interval_ms / 1000)
        stream = None

        try:
            # 验证设备
            if device is not None:
                dev_info = _sd.query_devices(device)
                if dev_info["max_input_channels"] < channels:
                    max_ch = dev_info["max_input_channels"]
                    msg = json.dumps({"error": f"设备 {device} 仅支持 {max_ch} 声道"})
                    yield f"data: {msg}\n\n"
                    return

            stream = _sd.InputStream(
                device=device,
                samplerate=samplerate,
                channels=channels,
                blocksize=block_size,
                dtype="float32",
            )
            stream.start()

            yield f"data: {json.dumps({'event': 'started', 'device': device, 'samplerate': samplerate, 'blocksize': block_size})}\n\n"

            def _blocking_read():
                """在线程池中读取音频块, 可被 stop_event 中断"""
                try:
                    if stop_event.is_set():
                        return None
                    return stream.read(block_size)
                except Exception:
                    return None

            while not stop_event.is_set():
                # 在线程池中读取音频块 (阻塞 I/O)
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, _blocking_read),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    # 读取超时, 检查是否需要停止
                    if stop_event.is_set():
                        break
                    continue

                if result is None:
                    break
                data, overflowed = result

                rms = float(_np.sqrt(_np.mean(data ** 2)))
                peak = float(_np.max(_np.abs(data)))

                # dBFS
                rms_db = float(20 * _np.log10(rms + 1e-10))
                peak_db = float(20 * _np.log10(peak + 1e-10))

                # 归一化百分比 (0-100, 基于 -60dB ~ 0dB 范围)
                rms_pct = max(0.0, min(100.0, (rms_db + 60) / 60 * 100))

                payload = {
                    "rms": round(rms, 6),
                    "peak": round(peak, 6),
                    "rms_db": round(rms_db, 1),
                    "peak_db": round(peak_db, 1),
                    "rms_pct": round(rms_pct, 1),
                    "overflowed": overflowed,
                    "ts": round(time.time(), 3),
                }
                yield f"data: {json.dumps(payload)}\n\n"

                # 小延迟让事件循环呼吸
                await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            logger.info("音量流被客户端关闭")
        except Exception as e:
            logger.error("音量流异常: %s", e)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            stop_event.set()  # 确保线程退出
            if stop_event in _active_stream_stops:
                _active_stream_stops.remove(stop_event)
            if stream is not None:
                try:
                    stream.abort()   # abort() 立即停止, 不等缓冲区排空
                    stream.close()
                except Exception:
                    pass

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ───────────────────────── 4. 短时录音测试 ─────────────────────────

@router.post("/test-capture")
async def test_capture(
    device: Optional[int] = Query(None),
    duration: float = Query(2.0, ge=0.5, le=10.0, description="录音时长 (秒)"),
    samplerate: int = Query(16000),
    channels: int = Query(1),
):
    """
    录制一小段音频, 返回统计信息 (不返回原始数据, 仅用于验证设备可用性).
    """
    if not _sd_available():
        return {"success": False, "error": "sounddevice not available"}

    loop = asyncio.get_event_loop()

    try:
        def _record():
            return _sd.rec(
                int(samplerate * duration),
                samplerate=samplerate,
                channels=channels,
                dtype="float32",
                device=device,
            )

        recording = await loop.run_in_executor(None, _record)
        # 等待录制完成
        await loop.run_in_executor(None, _sd.wait)

        rms = float(_np.sqrt(_np.mean(recording ** 2)))
        peak = float(_np.max(_np.abs(recording)))
        rms_db = float(20 * _np.log10(rms + 1e-10))
        peak_db = float(20 * _np.log10(peak + 1e-10))
        duration_actual = len(recording) / samplerate

        # 简单静音检测
        is_silent = rms_db < -50

        return {
            "success": True,
            "duration": round(duration_actual, 2),
            "samplerate": samplerate,
            "channels": channels,
            "samples": len(recording),
            "rms": round(rms, 6),
            "peak": round(peak, 6),
            "rms_db": round(rms_db, 1),
            "peak_db": round(peak_db, 1),
            "is_silent": is_silent,
            "device": device,
        }
    except Exception as e:
        logger.error("录音测试失败: %s", e)
        return {"success": False, "error": str(e)}


# ───────────────────────── 5. 录音并返回音频文件 ─────────────────────────

@router.post("/record-audio")
async def record_audio(
    device: Optional[int] = Query(None),
    duration: float = Query(2.0, ge=0.5, le=30.0, description="录音时长 (秒)"),
    samplerate: int = Query(16000),
    channels: int = Query(1),
):
    """
    录制一小段音频并返回 WAV 文件. 统计信息通过响应头返回.
    """
    import io
    import struct

    if not _sd_available():
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"detail": "sounddevice not available"})

    loop = asyncio.get_event_loop()

    try:
        def _record():
            return _sd.rec(
                int(samplerate * duration),
                samplerate=samplerate,
                channels=channels,
                dtype="float32",
                device=device,
            )

        recording = await loop.run_in_executor(None, _record)
        await loop.run_in_executor(None, _sd.wait)

        # 计算统计信息
        rms = float(_np.sqrt(_np.mean(recording ** 2)))
        peak = float(_np.max(_np.abs(recording)))
        rms_db = round(float(20 * _np.log10(rms + 1e-10)), 1)
        peak_db = round(float(20 * _np.log10(peak + 1e-10)), 1)
        duration_actual = round(len(recording) / samplerate, 2)
        is_silent = rms_db < -50

        # 将 float32 → int16 PCM
        pcm = _np.clip(recording, -1.0, 1.0)
        pcm_int16 = (pcm * 32767).astype(_np.int16)

        # 构建 WAV 文件
        buf = io.BytesIO()
        num_samples = pcm_int16.shape[0]
        data_size = num_samples * channels * 2  # 2 bytes per int16 sample
        # RIFF header
        buf.write(b"RIFF")
        buf.write(struct.pack("<I", 36 + data_size))
        buf.write(b"WAVE")
        # fmt chunk
        buf.write(b"fmt ")
        buf.write(struct.pack("<I", 16))  # chunk size
        buf.write(struct.pack("<H", 1))   # PCM format
        buf.write(struct.pack("<H", channels))
        buf.write(struct.pack("<I", samplerate))
        buf.write(struct.pack("<I", samplerate * channels * 2))  # byte rate
        buf.write(struct.pack("<H", channels * 2))  # block align
        buf.write(struct.pack("<H", 16))  # bits per sample
        # data chunk
        buf.write(b"data")
        buf.write(struct.pack("<I", data_size))
        buf.write(pcm_int16.tobytes())

        buf.seek(0)

        headers = {
            "X-Audio-Duration": str(duration_actual),
            "X-Audio-RMS-DB": str(rms_db),
            "X-Audio-Peak-DB": str(peak_db),
            "X-Audio-Is-Silent": str(is_silent).lower(),
            "X-Audio-Samplerate": str(samplerate),
            "X-Audio-Channels": str(channels),
            "Content-Disposition": f'attachment; filename="recording_{int(time.time())}.wav"',
            "Access-Control-Expose-Headers": "X-Audio-Duration, X-Audio-RMS-DB, X-Audio-Peak-DB, X-Audio-Is-Silent, X-Audio-Samplerate, X-Audio-Channels",
        }

        return StreamingResponse(
            buf,
            media_type="audio/wav",
            headers=headers,
        )
    except Exception as e:
        logger.error("录音失败: %s", e)
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": str(e)})
