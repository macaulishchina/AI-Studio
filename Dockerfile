# Studio (设计院) Dockerfile
FROM docker.1ms.run/library/python:3.11-slim

WORKDIR /app

# 系统依赖
#   - git curl docker.io: 基础工具 + Docker CLI
#   - libportaudio2 libasound2-dev: PortAudio + ALSA (sounddevice 音频采集)
#   - libgl1 libglib2.0-0: OpenCV 运行时依赖
#   - v4l-utils: V4L2 摄像头工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl docker.io \
    libportaudio2 libasound2-dev \
    libgl1 libglib2.0-0 \
    v4l-utils \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/requirements.txt

# 复制应用代码 (保持 studio 包结构, 使 from studio.backend.xxx 正常工作)
COPY . ./studio/

# 创建数据目录
RUN mkdir -p /data/plans /data/db-backups /data/uploads

ENV PYTHONPATH=/app
ENV STUDIO_DATA_PATH=/data

EXPOSE 8002

# 注意: 如需容器内访问宿主 USB 摄像头/麦克风，运行时需要:
#   --device /dev/video0 --device /dev/snd
#   --group-add audio --group-add video
# 示例:
#   docker run -d --name ai-studio -p 8002:8002 \
#     --device /dev/video0 --device /dev/snd \
#     --group-add $(stat -c '%g' /dev/snd/timer) \
#     --group-add $(stat -c '%g' /dev/video0) \
#     -v ai-studio-data:/data \
#     ai-studio
CMD ["uvicorn", "studio.backend.main:app", "--host", "0.0.0.0", "--port", "8002"]
