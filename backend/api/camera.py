"""
服务端摄像头 API
- 设备枚举 (/dev/video*)
- 单帧截图 (JPEG)
- MJPEG 实时流
"""
import asyncio
import io
import json
import logging
import os
import time
from typing import Optional
from collections import defaultdict
import threading

from fastapi import APIRouter, Query
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/studio-api/camera", tags=["camera"])

# Per-device locks to prevent concurrent open attempts (many cameras don't allow
# multiple handles). Use threading.Lock because open/read happens in executor.
_device_locks = defaultdict(threading.Lock)
# Events to request running stream to stop for a device. Keyed by device index.
_stream_stop_events: dict[int, threading.Event] = {}


def shutdown_all_streams():
    """通知所有活跃的摄像头流停止, 供 app shutdown 调用."""
    for evt in _stream_stop_events.values():
        evt.set()
    _stream_stop_events.clear()

# ── 可选依赖: OpenCV ──
_cv2 = None
_cv2_import_error: Optional[str] = None

try:
    import cv2
    _cv2 = cv2
except ImportError as e:
    _cv2_import_error = str(e)
    logger.warning("opencv-python-headless 未安装, 摄像头功能不可用: %s", e)
except Exception as e:
    _cv2_import_error = str(e)
    logger.warning("OpenCV 初始化失败: %s", e)


def _cv2_available() -> bool:
    return _cv2 is not None


# ───────────────────────── 1. 设备枚举 ─────────────────────────

@router.get("/devices")
async def list_camera_devices():
    """列出服务端所有 V4L2 / 摄像头设备"""
    devices = []

    # 扫描 /dev/video*
    dev_entries = sorted(
        (e for e in os.listdir("/dev") if e.startswith("video")),
    )
    for entry in dev_entries:
        dev_path = f"/dev/{entry}"
        idx = int(entry.replace("video", ""))
        info: dict = {
            "index": idx,
            "path": dev_path,
            "name": entry,
            "readable": os.access(dev_path, os.R_OK),
            "writable": os.access(dev_path, os.W_OK),
        }

        # 读取 sysfs 名称
        sysfs_name = f"/sys/class/video4linux/{entry}/name"
        if os.path.exists(sysfs_name):
            try:
                info["device_name"] = open(sysfs_name).read().strip()
            except Exception:
                pass

        # 用 OpenCV 快速探测能否打开 (线程 + 超时, 防止卡死)
        if _cv2_available() and info["readable"]:
            def _probe(dev_idx: int) -> dict:
                result: dict = {}
                try:
                    cap = _cv2.VideoCapture(dev_idx, _cv2.CAP_V4L2)
                    if cap.isOpened():
                        result["can_open"] = True
                        result["width"] = int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH))
                        result["height"] = int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT))
                        result["fps"] = round(cap.get(_cv2.CAP_PROP_FPS), 1)
                        cap.release()
                    else:
                        result["can_open"] = False
                except Exception as e:
                    result["can_open"] = False
                    result["error"] = str(e)
                return result

            try:
                loop = asyncio.get_event_loop()
                probe_result = await asyncio.wait_for(
                    loop.run_in_executor(None, _probe, idx),
                    timeout=3.0,
                )
                info.update(probe_result)
            except asyncio.TimeoutError:
                info["can_open"] = False
                info["error"] = "探测超时 (3s)"

        devices.append(info)

    return {
        "available": _cv2_available(),
        "opencv_error": _cv2_import_error,
        "devices": devices,
    }


# ───────────────────────── 2. 单帧截图 ─────────────────────────

@router.get("/snapshot")
async def camera_snapshot(
    device: int = Query(0, description="摄像头索引 (/dev/videoN)"),
    width: Optional[int] = Query(None, description="请求宽度"),
    height: Optional[int] = Query(None, description="请求高度"),
    quality: int = Query(85, ge=10, le=100, description="JPEG 质量"),
):
    """拍摄一帧 JPEG 快照"""
    if not _cv2_available():
        return Response(
            content=json.dumps({"error": "OpenCV not available", "detail": _cv2_import_error}),
            media_type="application/json",
            status_code=503,
        )

    loop = asyncio.get_event_loop()

    def _capture():
        lock = _device_locks[device]
        acquired = lock.acquire(timeout=2)
        if not acquired:
            return None, f"设备 /dev/video{device} 正在被占用"
        cap = _cv2.VideoCapture(device, _cv2.CAP_V4L2)
        if not cap.isOpened():
            # release lock if open failed
            lock.release()
            return None, f"无法打开 /dev/video{device}"
        try:
            if width:
                cap.set(_cv2.CAP_PROP_FRAME_WIDTH, width)
            if height:
                cap.set(_cv2.CAP_PROP_FRAME_HEIGHT, height)
            # 读几帧让自动曝光稳定
            for _ in range(3):
                cap.read()
            ret, frame = cap.read()
            if not ret:
                return None, "读取帧失败"
            _, buf = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, quality])
            return buf.tobytes(), None
        finally:
            try:
                cap.release()
            finally:
                # ensure lock released
                try:
                    lock.release()
                except Exception:
                    pass

    try:
        jpeg_data, error = await asyncio.wait_for(
            loop.run_in_executor(None, _capture), timeout=8.0,
        )
    except asyncio.TimeoutError:
        return Response(
            content=json.dumps({"error": "拍照超时 (8s), 摄像头可能无法访问"}),
            media_type="application/json",
            status_code=504,
        )

    if error:
        return Response(
            content=json.dumps({"error": error}),
            media_type="application/json",
            status_code=500,
        )

    return Response(
        content=jpeg_data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Timestamp": str(time.time()),
        },
    )


# ───────────────────────── 3. MJPEG 实时流 ─────────────────────────

@router.get("/stream")
async def camera_stream(
    device: int = Query(0, description="摄像头索引"),
    fps: int = Query(10, ge=1, le=30, description="目标帧率"),
    width: Optional[int] = Query(None),
    height: Optional[int] = Query(None),
    quality: int = Query(70, ge=10, le=100),
):
    """
    MJPEG 实时流 — 标准 multipart/x-mixed-replace.
    可直接放入 <img src="..."> 标签使用.
    """
    if not _cv2_available():
        return Response(
            content=json.dumps({"error": "OpenCV not available"}),
            media_type="application/json",
            status_code=503,
        )

    loop = asyncio.get_event_loop()

    def _open_blocking():
        lock = _device_locks[device]
        acquired = lock.acquire(blocking=False)
        if not acquired:
            return None
        cap = _cv2.VideoCapture(device, _cv2.CAP_V4L2)
        if width:
            cap.set(_cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            cap.set(_cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not cap.isOpened():
            try:
                lock.release()
            except Exception:
                pass
            return None
        return (cap, True)

    # If a previous stream exists, signal it to stop and wait shortly for the
    # device lock to be released before attempting to open. This allows the UI
    # to switch parameters (fps/size) by requesting the previous stream to exit.
    prev_event = _stream_stop_events.get(device)
    if prev_event is not None:
        try:
            prev_event.set()
        except Exception:
            pass

    # wait up to 3s for lock to be released by previous stream
    def _wait_for_unlock(timeout: float = 3.0) -> bool:
        import time as _time
        start = _time.time()
        while _time.time() - start < timeout:
            if not _device_locks[device].locked():
                return True
            _time.sleep(0.1)
        return False

    unlocked = await asyncio.get_event_loop().run_in_executor(None, _wait_for_unlock, 3.0)
    if not unlocked:
        # give a final try to open; if still locked, report busy
        return Response(content=json.dumps({"error": "device busy or cannot open"}), media_type="application/json", status_code=409)

    try:
        res = await asyncio.wait_for(loop.run_in_executor(None, _open_blocking), timeout=5.0)
    except asyncio.TimeoutError:
        return Response(content=json.dumps({"error": "open timeout"}), media_type="application/json", status_code=504)

    if not res:
        return Response(content=json.dumps({"error": "device busy or cannot open"}), media_type="application/json", status_code=409)

    cap, acquired = res

    # create an event used to request this stream to stop from future requests
    stop_event = threading.Event()
    _stream_stop_events[device] = stop_event

    async def _generate():
        try:
            interval = 1.0 / fps

            while not stop_event.is_set():
                def _read():
                    if stop_event.is_set():
                        return None
                    ret, frame = cap.read()
                    # if a stop has been requested for this stream, end
                    if stop_event.is_set():
                        return None
                    if not ret:
                        return None
                    _, buf = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, quality])
                    return buf.tobytes()

                try:
                    jpeg = await asyncio.wait_for(
                        loop.run_in_executor(None, _read),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    if stop_event.is_set():
                        break
                    continue

                if jpeg is None:
                    break

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )

                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.info("摄像头流被客户端关闭")
        except Exception as e:
            logger.error("摄像头流异常: %s", e)
        finally:
            try:
                cap.release()
            finally:
                if acquired:
                    try:
                        _device_locks[device].release()
                    except Exception:
                        pass
            # clear stop event if it is still the one we set
            try:
                cur = _stream_stop_events.get(device)
                if cur is stop_event:
                    del _stream_stop_events[device]
            except Exception:
                pass

    return StreamingResponse(
        _generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ───────────────────────── 4. 摄像头详细信息 ─────────────────────────

@router.get("/info")
async def camera_info(device: int = Query(0)):
    """获取摄像头详细参数"""
    result: dict = {"device": device, "path": f"/dev/video{device}"}

    # sysfs info
    sysfs_name = f"/sys/class/video4linux/video{device}/name"
    if os.path.exists(sysfs_name):
        result["device_name"] = open(sysfs_name).read().strip()

    if not _cv2_available():
        result["opencv_available"] = False
        result["error"] = _cv2_import_error
        return result

    loop = asyncio.get_event_loop()

    def _get_info():
        lock = _device_locks[device]
        acquired = lock.acquire(timeout=1)
        if not acquired:
            return {"can_open": False, "busy": True}
        cap = _cv2.VideoCapture(device, _cv2.CAP_V4L2)
        if not cap.isOpened():
            try:
                lock.release()
            except Exception:
                pass
            return {"can_open": False}
        try:
            info = {
                "can_open": True,
                "width": int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps": round(cap.get(_cv2.CAP_PROP_FPS), 1),
                "fourcc": int(cap.get(_cv2.CAP_PROP_FOURCC)),
                "backend": cap.getBackendName(),
            }
            # 尝试读一帧确认可用
            ret, _ = cap.read()
            info["can_read"] = ret
            return info
        finally:
            try:
                cap.release()
            finally:
                try:
                    lock.release()
                except Exception:
                    pass

    try:
        cam_info = await asyncio.wait_for(
            loop.run_in_executor(None, _get_info), timeout=5.0,
        )
    except asyncio.TimeoutError:
        result["can_open"] = False
        result["error"] = "探测超时 (5s)"
        return result
    result.update(cam_info)
    return result
