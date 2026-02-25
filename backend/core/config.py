"""
设计院 (Studio) - 全局配置
通用项目设计院，不绑定任何具体项目
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class StudioSettings:
    """设计院配置"""
    # GitHub (可选 — 如果项目使用 GitHub)
    github_token: str = os.environ.get("GITHUB_TOKEN", "")
    github_repo: str = os.environ.get("GITHUB_REPO", "")  # e.g. "owner/repo"

    # 工作区路径
    workspace_path: str = os.environ.get("WORKSPACE_PATH", "/workspace")

    # 数据存储
    data_path: str = os.environ.get("STUDIO_DATA_PATH", "/data")
    plans_path: str = ""
    db_backups_path: str = ""
    uploads_path: str = ""

    # GitHub Models API
    github_models_endpoint: str = "https://models.inference.ai.azure.com"

    # ── 部署配置 ──
    health_check_timeout: int = 60
    health_check_retries: int = 3
    health_check_interval: int = 5

    # 受保护的服务 (部署流水线永远不触碰)
    protected_services: List[str] = field(default_factory=lambda: ["gateway", "studio"])

    # 部署目标服务 (docker compose build/up 的服务列表)
    deploy_services: List[str] = field(
        default_factory=lambda: os.environ.get(
            "DEPLOY_SERVICES", "frontend,backend"
        ).split(",")
    )

    # 健康检查端点 [{url, name}]  — 空列表则跳过
    deploy_health_checks: List[Dict[str, str]] = field(
        default_factory=lambda: _parse_health_checks(
            os.environ.get("DEPLOY_HEALTH_CHECKS", "")
        )
    )

    # git 主分支名
    deploy_git_branch: str = os.environ.get("DEPLOY_GIT_BRANCH", "master")

    # 快照相关
    snapshot_db_paths: List[str] = field(
        default_factory=lambda: [
            p.strip() for p in os.environ.get("SNAPSHOT_DB_PATHS", "").split(",") if p.strip()
        ]
    )
    docker_image_prefix: str = os.environ.get("DOCKER_IMAGE_PREFIX", "")  # e.g. "goldennest"

    # ── 设计院认证 ──
    studio_admin_user: str = os.environ.get("STUDIO_ADMIN_USER", "admin")
    studio_admin_pass: str = os.environ.get("STUDIO_ADMIN_PASS", "")
    studio_secret_key: str = os.environ.get("STUDIO_SECRET_KEY", "")
    studio_token_expire_days: int = int(os.environ.get("STUDIO_TOKEN_EXPIRE_DAYS", "7"))

    # 主项目 API (可选 — 用于 SSO token 验证，留空禁用)
    main_api_url: str = os.environ.get("MAIN_API_URL", "")
    # SSO token 在 localStorage 中的 key (主项目存储 JWT 的键名，默认 "token")
    sso_token_key: str = os.environ.get("SSO_TOKEN_KEY", "token")

    # 通用 Git 仓库克隆 URL (可选 — 支持 GitLab/Gitea/自建等任意 Git 仓库)
    # 留空时回退到 GITHUB_REPO 构造 GitHub URL，都为空则禁用 Git 克隆功能
    # 示例: https://gitlab.example.com/org/repo.git 或 git@gitee.com:org/repo.git
    git_clone_url: str = os.environ.get("GIT_CLONE_URL", "")

    # ── SVN 支持 (可选) ──
    svn_repo_url: str = os.environ.get("SVN_REPO_URL", "")        # SVN 仓库 URL
    svn_username: str = os.environ.get("SVN_USERNAME", "")        # SVN 用户名
    svn_password: str = os.environ.get("SVN_PASSWORD", "")        # SVN 密码
    svn_trunk_path: str = os.environ.get("SVN_TRUNK_PATH", "trunk")  # trunk 相对路径

    # 版本控制类型: auto (自动检测 .git/.svn) | git | svn | none
    vcs_type: str = os.environ.get("VCS_TYPE", "auto")

    def __post_init__(self):
        # 相对路径统一按项目根解析，避免受当前工作目录影响
        data_path = Path(self.data_path)
        if not data_path.is_absolute():
            data_path = (PROJECT_ROOT / data_path).resolve()
        self.data_path = str(data_path)

        workspace_path = Path(self.workspace_path)
        if not workspace_path.is_absolute():
            workspace_path = (PROJECT_ROOT / workspace_path).resolve()
        self.workspace_path = str(workspace_path)

        self.plans_path = os.path.join(self.data_path, "plans")
        self.db_backups_path = os.path.join(self.data_path, "db-backups")
        self.uploads_path = os.path.join(self.data_path, "uploads")
        os.makedirs(self.plans_path, exist_ok=True)
        os.makedirs(self.db_backups_path, exist_ok=True)
        os.makedirs(self.uploads_path, exist_ok=True)
        # 自动生成 secret key
        if not self.studio_secret_key:
            import secrets
            self.studio_secret_key = secrets.token_urlsafe(32)
        # 自动生成管理员密码 (首次部署)
        if not self.studio_admin_pass:
            import secrets
            self.studio_admin_pass = secrets.token_urlsafe(16)
            import logging
            logger = logging.getLogger("studio.config")
            logger.warning(
                f"\n{'='*60}\n"
                f"⚠️  设计院管理员密码已自动生成:\n"
                f"    用户名: {self.studio_admin_user}\n"
                f"    密码: {self.studio_admin_pass}\n"
                f"    请设置环境变量 STUDIO_ADMIN_PASS 以使用固定密码\n"
                f"{'='*60}"
            )
            # 也输出到 stdout 确保 docker logs 可见
            print(f"\n⚠️  Studio Admin: {self.studio_admin_user} / {self.studio_admin_pass}\n", flush=True)


def _parse_health_checks(env_val: str) -> list:
    """
    解析 DEPLOY_HEALTH_CHECKS 环境变量
    格式: "name=url;name=url" 如 "backend=http://backend:8000/api/health;frontend=http://frontend:80"
    空字符串 → 空列表 (跳过健康检查)
    """
    if not env_val.strip():
        return []
    checks = []
    for item in env_val.split(";"):
        item = item.strip()
        if "=" in item:
            name, url = item.split("=", 1)
            checks.append({"name": name.strip(), "url": url.strip()})
    return checks


settings = StudioSettings()
