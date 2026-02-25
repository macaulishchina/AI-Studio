#!/usr/bin/env python3
"""
AI-Studio 开发模式启动脚本 (后端)

自动处理:
1. PYTHONPATH 设置 (使 studio.backend.xxx 导入正常工作)
2. 开发数据目录创建
3. 环境变量默认值
4. uvicorn 热重载启动
"""
import os
import sys
from pathlib import Path


def main():
    # ── 路径计算 ──
    # 项目根目录 (本脚本所在目录)
    project_root = Path(__file__).resolve().parent
    # 直接使用项目根作为 PYTHONPATH。
    # 项目内置 studio/backend 桥接包，无需再创建父目录链接。
    pythonpath = str(project_root)

    # ── 开发数据目录 ──
    dev_data = project_root / "dev-data"
    dev_data.mkdir(exist_ok=True)
    (dev_data / "plans").mkdir(exist_ok=True)
    (dev_data / "db-backups").mkdir(exist_ok=True)
    (dev_data / "uploads").mkdir(exist_ok=True)

    # ── 环境变量 ──
    env = os.environ.copy()
    env["PYTHONPATH"] = pythonpath
    env.setdefault("STUDIO_DATA_PATH", str(dev_data))
    env.setdefault("WORKSPACE_PATH", str(project_root))
    env.setdefault("STUDIO_ADMIN_USER", "admin")
    # 开发模式使用固定密码 (方便)
    env.setdefault("STUDIO_ADMIN_PASS", "admin123")
    env.setdefault("STUDIO_SECRET_KEY", "dev-secret-key-not-for-production")

    # ── 启动信息 ──
    print("=" * 60)
    print("🤖 AI-Studio (设计院) — 开发模式")
    print("=" * 60)
    print(f"  项目目录:   {project_root}")
    print(f"  PYTHONPATH: {pythonpath}")
    print(f"  数据目录:   {env['STUDIO_DATA_PATH']}")
    print(f"  工作区:     {env['WORKSPACE_PATH']}")
    print(f"  管理员:     {env.get('STUDIO_ADMIN_USER', 'admin')} / {env.get('STUDIO_ADMIN_PASS', '(auto)')}")
    print(f"  后端地址:   http://localhost:8002")
    print(f"  API 文档:   http://localhost:8002/studio-api/docs")
    print("=" * 60)

    # ── 启动 uvicorn ──
    cmd = [
        sys.executable, "-m", "uvicorn",
        "studio.backend.main:app",
        "--host", "0.0.0.0",
        "--port", "8002",
        "--reload",
        "--reload-dir", str(project_root / "backend"),
    ]

    print(f"\n▶ {' '.join(cmd)}\n")

    try:
        import subprocess
        result = subprocess.run(cmd, env=env, cwd=str(project_root))
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\n\n⏹ 后端已停止")
        sys.exit(0)


if __name__ == "__main__":
    main()
