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
import subprocess
from pathlib import Path


def main():
    # ── 路径计算 ──
    # 项目根目录 (本脚本所在目录)
    project_root = Path(__file__).resolve().parent
    # studio 包的父目录 (PYTHONPATH 需要指向这里)
    # Docker 中是 /app (项目复制到 /app/studio/)
    # 本地开发: 项目文件夹名作为包名需要是 "studio"，
    # 或者我们把父目录加入 PYTHONPATH 并创建符号链接/使用实际目录名
    #
    # 策略: 将项目根目录的 *父目录* 加入 PYTHONPATH，
    # 然后将项目根目录重命名/软链为 "studio"
    # 但为了不侵入用户的文件系统，我们用另一种方式:
    # 创建临时的包映射目录

    parent_dir = project_root.parent
    studio_pkg_dir = parent_dir / "studio"

    # 如果项目根目录名不是 "studio"，需要创建符号链接
    if project_root.name != "studio":
        if sys.platform == "win32":
            # Windows: 使用 junction (不需要管理员权限)
            if not studio_pkg_dir.exists():
                print(f"📁 创建目录链接: {studio_pkg_dir} → {project_root}")
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(studio_pkg_dir), str(project_root)],
                    check=True,
                )
        else:
            # Linux/macOS: 符号链接
            if not studio_pkg_dir.exists():
                print(f"📁 创建符号链接: {studio_pkg_dir} → {project_root}")
                studio_pkg_dir.symlink_to(project_root)

        pythonpath = str(parent_dir)
    else:
        pythonpath = str(parent_dir)

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
        result = subprocess.run(cmd, env=env, cwd=str(parent_dir))
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print("\n\n⏹ 后端已停止")
        sys.exit(0)


if __name__ == "__main__":
    main()
