"""`studio.backend` 桥接包。

将 `studio.backend.*` 的模块查找路径映射到项目根下的 `backend/` 目录，
从而不再需要在父目录创建 `studio` 软链接/junction。
"""

from pathlib import Path


_project_root = Path(__file__).resolve().parents[2]
_backend_dir = _project_root / "backend"

if _backend_dir.is_dir():
    __path__.append(str(_backend_dir))
