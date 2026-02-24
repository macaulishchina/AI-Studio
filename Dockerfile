# Studio (设计院) Dockerfile
FROM docker.1ms.run/library/python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl docker.io \
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

CMD ["uvicorn", "studio.backend.main:app", "--host", "0.0.0.0", "--port", "8002"]
