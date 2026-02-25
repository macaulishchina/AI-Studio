"""
设计院 (Studio) - 工作区管理服务

为每个项目的不同阶段提供独立的 git 工作区：
- 讨论阶段：首次使用默认 /workspace，迭代时使用独立克隆
- 审查阶段：克隆实施分支到独立目录
- 隔离性：每个项目/阶段的工作区互不影响

VCS 支持: Git + SVN 双引擎，自动检测
"""
import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import Counter
from functools import partial
from typing import Dict, List, Optional, Tuple
from sqlalchemy import select

from studio.backend.core.config import settings

logger = logging.getLogger(__name__)


def _decode_output(raw: bytes) -> str:
    """智能解码子进程输出 (优先 UTF-8, 回退到系统编码 GBK 等)"""
    if not raw:
        return ""
    # 先尝试 UTF-8 (严格模式)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # 回退到系统默认编码 (中文 Windows: cp936/gbk)
    import locale
    sys_enc = locale.getpreferredencoding(False)
    try:
        return raw.decode(sys_enc)
    except (UnicodeDecodeError, LookupError):
        pass
    # 最终回退
    return raw.decode("utf-8", errors="replace")


# 工作区根目录
WORKSPACES_ROOT = os.path.join(settings.data_path, "workspaces")

# ── 工作区概览缓存 ──
_overview_cache: Dict = {}
_overview_cache_ts: float = 0
_OVERVIEW_CACHE_TTL = 60  # 秒


def clear_overview_cache():
    """清除工作区概览缓存 (切换工作目录时调用)"""
    global _overview_cache, _overview_cache_ts
    _overview_cache = {}
    _overview_cache_ts = 0


async def _resolve_active_workspace_path() -> str:
    """解析当前生效工作目录: 优先 DB 活跃目录, 否则 settings.workspace_path。"""
    try:
        from studio.backend.core.database import async_session_maker
        from studio.backend.models import WorkspaceDir
        async with async_session_maker() as db:
            row = (await db.execute(
                select(WorkspaceDir.path).where(WorkspaceDir.is_active == True).limit(1)
            )).first()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return settings.workspace_path

# ── 文件扩展名 → 语言映射 ──
_EXT_LANG_MAP = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".vue": "Vue",
    ".jsx": "React JSX",
    ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C/C++ Header", ".hpp": "C/C++ Header", ".hxx": "C/C++ Header", ".hh": "C/C++ Header",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".r": "R", ".R": "R",
    ".lua": "Lua",
    ".dart": "Dart",
    ".sql": "SQL",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".bat": "Batch", ".cmd": "Batch", ".ps1": "PowerShell",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".sass": "SASS", ".less": "Less",
    ".json": "JSON",
    ".xml": "XML",
    ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML",
    ".md": "Markdown", ".mdx": "Markdown",
    ".proto": "Protobuf",
    ".graphql": "GraphQL", ".gql": "GraphQL",
    ".dockerfile": "Dockerfile",
    ".tf": "Terraform",
    ".svelte": "Svelte",
}

# ── 扫描时忽略的目录 ──
_IGNORE_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", "venv", ".venv", "env", ".env", "dist", "build", ".next",
    ".nuxt", "target", "out", "bin", "obj", ".idea", ".vscode", ".gradle",
    "vendor", "bower_components", ".terraform", "coverage", ".cache",
}

# ── 关键文件列表 (检测是否存在) ──
_KEY_FILES = [
    "CLAUDE.md", "COPILOT.md", "README.md", "README.rst",
    "package.json", "pyproject.toml", "requirements.txt", "setup.py",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "Makefile", "CMakeLists.txt",
    ".gitignore", ".editorconfig",
    "tsconfig.json", "vite.config.ts", "webpack.config.js",
    "CONTRIBUTING.md", "LICENSE",
]


def _ensure_workspaces_root():
    """确保工作区根目录存在"""
    os.makedirs(WORKSPACES_ROOT, exist_ok=True)


def get_review_workspace_path(project_id: int) -> str:
    """获取审查阶段的工作区路径"""
    return os.path.join(WORKSPACES_ROOT, f"project-{project_id}-review")


def get_iteration_workspace_path(project_id: int, iteration: int) -> str:
    """获取迭代讨论的工作区路径"""
    return os.path.join(WORKSPACES_ROOT, f"project-{project_id}-iter-{iteration}")


def get_effective_workspace(project) -> str:
    """
    获取项目当前有效的工作区路径。
    优先使用 project.workspace_dir，否则使用全局默认 /workspace
    """
    ws = getattr(project, 'workspace_dir', None)
    if ws and os.path.isdir(ws):
        return ws
    return settings.workspace_path


def _run_git_sync(cmd: list, cwd: str, timeout: int = 120) -> Tuple[int, str, str]:
    """同步执行 git 命令 (在线程池中调用)"""
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, timeout=timeout, env=env,
        )
        return (
            result.returncode,
            _decode_output(result.stdout),
            _decode_output(result.stderr),
        )
    except subprocess.TimeoutExpired:
        return -1, "", "git 命令超时"
    except FileNotFoundError:
        return -1, "", "git 未安装或不在 PATH 中"
    except Exception as e:
        return -1, "", str(e)


async def _run_git(cmd: list, cwd: str, timeout: int = 120) -> Tuple[int, str, str]:
    """异步执行 git 命令 (通过线程池, 兼容 Windows)"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_run_git_sync, cmd, cwd, timeout))


def _build_clone_url(repo: str, token: str, provider: str = "github", gitlab_url: str = "https://gitlab.com") -> str:
    """
    构建 clone URL
    优先使用 GIT_CLONE_URL 环境变量 (支持任意 Git 仓库)
    否则回退到 GitHub repo 格式 (owner/repo)
    """
    # 优先使用通用 Git 克隆 URL
    if settings.git_clone_url:
        url = settings.git_clone_url
        # 如果是 HTTPS URL 且有 token, 注入认证信息
        if token and url.startswith("https://"):
            # https://example.com/repo.git → https://x-access-token:TOKEN@example.com/repo.git
            url = url.replace("https://", f"https://x-access-token:{token}@", 1)
        return url
    provider = (provider or "github").lower()

    # 回退: 使用 provider repo 格式
    if not repo:
        raise ValueError("Git 仓库未配置。请设置 GIT_CLONE_URL 或对应平台仓库配置。")

    if provider == "gitlab":
        base = (gitlab_url or "https://gitlab.com").rstrip("/")
        path = repo if repo.endswith(".git") else f"{repo}.git"
        if token:
            # GitLab PAT 常用用户名 oauth2
            host = base.replace("https://", "", 1)
            return f"https://oauth2:{token}@{host}/{path}"
        return f"{base}/{path}"

    # 默认 GitHub
    if token:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


async def _resolve_project_git_config(project_id: int) -> Tuple[str, str, str, str]:
    """
    解析项目使用的 Git 仓库配置（GitHub / GitLab）。
    优先级:
    1) 项目 workspace_dir 对应的 WorkspaceDir.github_*（按目录隔离）
    2) 当前活跃 WorkspaceDir.github_*
    3) settings.github_*（仅兜底）
    """
    from studio.backend.core.database import async_session_maker
    from studio.backend.models import Project, WorkspaceDir

    project_ws = ""
    active_ws = None
    async with async_session_maker() as db:
        prj = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
        if prj and prj.workspace_dir:
            project_ws = os.path.normpath(prj.workspace_dir)

        if project_ws:
            ws = (await db.execute(
                select(WorkspaceDir).where(WorkspaceDir.path == project_ws).limit(1)
            )).scalar_one_or_none()
            if ws:
                provider = (ws.git_provider or "github").strip().lower()
                if provider == "gitlab":
                    return (
                        (ws.gitlab_repo or "").strip(),
                        (ws.gitlab_token or "").strip(),
                        "gitlab",
                        (ws.gitlab_url or "https://gitlab.com").strip(),
                    )
                return (
                    (ws.github_repo or "").strip(),
                    (ws.github_token or "").strip(),
                    "github",
                    "https://gitlab.com",
                )

        active_ws = (await db.execute(
            select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1)
        )).scalar_one_or_none()

    if active_ws:
        provider = (active_ws.git_provider or "github").strip().lower()
        if provider == "gitlab":
            return (
                (active_ws.gitlab_repo or "").strip(),
                (active_ws.gitlab_token or "").strip(),
                "gitlab",
                (active_ws.gitlab_url or "https://gitlab.com").strip(),
            )
        return (
            (active_ws.github_repo or "").strip(),
            (active_ws.github_token or "").strip(),
            "github",
            "https://gitlab.com",
        )

    provider = (settings.git_provider or "github").strip().lower()
    if provider == "gitlab":
        return (
            (settings.gitlab_repo or "").strip(),
            (settings.gitlab_token or "").strip(),
            "gitlab",
            (settings.gitlab_url or "https://gitlab.com").strip(),
        )
    return (
        (settings.github_repo or "").strip(),
        (settings.github_token or "").strip(),
        "github",
        "https://gitlab.com",
    )


async def prepare_review_workspace(
    project_id: int,
    branch_name: str,
    pr_number: Optional[int] = None,
) -> Dict:
    """
    准备审查阶段的工作区

    1. 克隆/更新仓库到 /data/workspaces/project-{id}-review/
    2. 切换到实施分支
    3. 获取变更文件列表和 diff 统计

    Returns:
        {
            "workspace_dir": "/data/workspaces/project-5-review",
            "branch": "copilot/fix-xxx",
            "base_branch": "main",
            "diff_stat": "+100 -50, 8 files changed",
            "changed_files": ["file1.py", "file2.vue", ...],
            "success": True,
            "message": "工作区已就绪"
        }
    """
    _ensure_workspaces_root()
    ws_path = get_review_workspace_path(project_id)
    repo, token, provider, gitlab_url = await _resolve_project_git_config(project_id)
    clone_url = _build_clone_url(repo, token, provider=provider, gitlab_url=gitlab_url)

    result = {
        "workspace_dir": ws_path,
        "branch": branch_name,
        "base_branch": "main",
        "diff_stat": "",
        "changed_files": [],
        "success": False,
        "message": "",
    }

    try:
        if os.path.isdir(os.path.join(ws_path, ".git")):
            # 已存在 → fetch + checkout
            logger.info(f"更新已有工作区: {ws_path}")
            rc, out, err = await _run_git(["git", "fetch", "--all", "--prune"], ws_path)
            if rc != 0:
                result["message"] = f"git fetch 失败: {err}"
                return result
            rc, out, err = await _run_git(["git", "checkout", branch_name], ws_path)
            if rc != 0:
                # 可能是远程分支，尝试 origin/
                rc, out, err = await _run_git(
                    ["git", "checkout", "-b", branch_name, f"origin/{branch_name}"],
                    ws_path,
                )
                if rc != 0:
                    result["message"] = f"git checkout 失败: {err}"
                    return result
            rc, out, err = await _run_git(["git", "pull", "--ff-only"], ws_path)
        else:
            # 全新克隆
            if os.path.exists(ws_path):
                shutil.rmtree(ws_path)
            logger.info(f"克隆仓库到: {ws_path}")
            rc, out, err = await _run_git(
                ["git", "clone", "--branch", branch_name, clone_url, ws_path],
                WORKSPACES_ROOT,
                timeout=300,
            )
            if rc != 0:
                result["message"] = f"git clone 失败: {err}"
                return result

        # 获取 diff 统计 (对比 main)
        rc, out, err = await _run_git(
            ["git", "diff", "--stat", "origin/main...HEAD"],
            ws_path,
        )
        if rc == 0 and out.strip():
            result["diff_stat"] = out.strip().split("\n")[-1].strip()  # 最后一行是总结

        # 获取变更文件列表
        rc, out, err = await _run_git(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            ws_path,
        )
        if rc == 0 and out.strip():
            result["changed_files"] = [f for f in out.strip().split("\n") if f]

        # 获取 base branch
        rc, out, err = await _run_git(
            ["git", "log", "--oneline", "-1", "origin/main"],
            ws_path,
        )
        if rc == 0:
            result["base_branch"] = "main"

        result["success"] = True
        result["message"] = f"审查工作区已就绪 ({len(result['changed_files'])} 个文件变更)"
        logger.info(f"审查工作区就绪: project={project_id}, branch={branch_name}, files={len(result['changed_files'])}")

    except Exception as e:
        logger.exception(f"准备审查工作区失败: {e}")
        result["message"] = f"准备工作区失败: {str(e)}"

    return result


async def prepare_iteration_workspace(
    project_id: int,
    iteration: int,
    branch_name: str,
) -> Dict:
    """
    准备迭代讨论的工作区 (基于当前实施分支)

    Returns:
        {
            "workspace_dir": "/data/workspaces/project-5-iter-1",
            "branch": "copilot/fix-xxx",
            "success": True,
            "message": "迭代工作区已就绪"
        }
    """
    _ensure_workspaces_root()
    ws_path = get_iteration_workspace_path(project_id, iteration)
    repo, token, provider, gitlab_url = await _resolve_project_git_config(project_id)
    clone_url = _build_clone_url(repo, token, provider=provider, gitlab_url=gitlab_url)

    result = {
        "workspace_dir": ws_path,
        "branch": branch_name,
        "success": False,
        "message": "",
    }

    try:
        if os.path.exists(ws_path):
            shutil.rmtree(ws_path)

        logger.info(f"克隆迭代工作区: {ws_path}, branch={branch_name}")
        rc, out, err = await _run_git(
            ["git", "clone", "--branch", branch_name, clone_url, ws_path],
            WORKSPACES_ROOT,
            timeout=300,
        )
        if rc != 0:
            result["message"] = f"git clone 失败: {err}"
            return result

        result["success"] = True
        result["message"] = f"迭代工作区已就绪 (基于 {branch_name})"
        logger.info(f"迭代工作区就绪: project={project_id}, iter={iteration}")

    except Exception as e:
        logger.exception(f"准备迭代工作区失败: {e}")
        result["message"] = f"准备工作区失败: {str(e)}"

    return result


async def get_workspace_git_info(workspace_dir: str) -> Dict:
    """获取工作区的 git 信息"""
    info = {
        "branch": "",
        "commit": "",
        "commit_short": "",
        "commit_message": "",
    }
    if not workspace_dir or not os.path.isdir(os.path.join(workspace_dir, ".git")):
        return info

    rc, out, _ = await _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], workspace_dir)
    if rc == 0:
        info["branch"] = out.strip()

    rc, out, _ = await _run_git(["git", "rev-parse", "HEAD"], workspace_dir)
    if rc == 0:
        info["commit"] = out.strip()
        info["commit_short"] = out.strip()[:8]

    rc, out, _ = await _run_git(["git", "log", "--oneline", "-1", "--format=%s"], workspace_dir)
    if rc == 0:
        info["commit_message"] = out.strip()

    return info


def cleanup_project_workspaces(project_id: int):
    """清理项目相关的所有工作区"""
    _ensure_workspaces_root()
    prefix = f"project-{project_id}"
    for entry in os.listdir(WORKSPACES_ROOT):
        if entry.startswith(prefix):
            path = os.path.join(WORKSPACES_ROOT, entry)
            if os.path.isdir(path):
                try:
                    shutil.rmtree(path)
                    logger.info(f"清理工作区: {path}")
                except Exception as e:
                    logger.warning(f"清理工作区失败: {path}: {e}")


# ======================== VCS 抽象层 ========================

def detect_vcs_type(workspace_dir: str) -> str:
    """
    检测工作区的版本控制类型
    返回: "git" | "svn" | "none"
    """
    configured = settings.vcs_type.lower().strip()
    if configured in ("git", "svn", "none"):
        return configured
    # auto 模式: 检查目录标记
    if os.path.isdir(os.path.join(workspace_dir, ".git")):
        return "git"
    if os.path.isdir(os.path.join(workspace_dir, ".svn")):
        return "svn"
    # SVN 1.7+ 只在工作拷贝根目录有 .svn，向上查找
    cur = os.path.abspath(workspace_dir)
    for _ in range(10):  # 最多向上 10 层
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        if os.path.isdir(os.path.join(parent, ".svn")):
            return "svn"
        cur = parent
    # 最后尝试运行 svn info 检测 (处理 SVN 外部挂载等情况)
    try:
        r = subprocess.run(
            ["svn", "info"], cwd=workspace_dir,
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            return "svn"
    except Exception:
        pass
    return "none"


async def _run_svn(cmd: list, cwd: str, timeout: int = 120) -> Tuple[int, str, str]:
    """执行 svn 命令 (非交互模式)"""
    env = {**os.environ}
    # 注入认证信息
    svn_cmd = list(cmd)
    if settings.svn_username:
        svn_cmd.extend(["--username", settings.svn_username])
    if settings.svn_password:
        svn_cmd.extend(["--password", settings.svn_password])
    svn_cmd.extend(["--non-interactive", "--no-auth-cache", "--trust-server-cert-failures=unknown-ca,cn-mismatch,expired,not-yet-valid,other"])

    def _run():
        try:
            result = subprocess.run(
                svn_cmd, cwd=cwd, capture_output=True, timeout=timeout, env=env,
            )
            return (
                result.returncode,
                _decode_output(result.stdout),
                _decode_output(result.stderr),
            )
        except subprocess.TimeoutExpired:
            return -1, "", "svn 命令超时"
        except FileNotFoundError:
            return -1, "", "svn 未安装或不在 PATH 中"
        except Exception as e:
            return -1, "", str(e)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


async def get_workspace_svn_info(workspace_dir: str) -> Dict:
    """获取工作区的 SVN 信息 (使用 --xml 输出, 始终 UTF-8 无编码问题)"""
    info = {
        "type": "svn",
        "branch": "",
        "commit": "",
        "commit_short": "",
        "commit_message": "",
        "url": "",
        "relative_url": "",
        "repo_root": "",
        "last_author": "",
    }
    if not workspace_dir:
        return info

    # ── svn info --xml ─────────────────────────────
    rc, out, err = await _run_svn(["svn", "info", "--xml"], workspace_dir, timeout=30)
    logger.debug(f"svn info --xml rc={rc}, out={out[:500] if out else ''}, err={err[:200] if err else ''}")
    if rc == 0 and out.strip():
        try:
            root = ET.fromstring(out)
            entry = root.find("entry")
            if entry is not None:
                url = entry.findtext("url", "")
                info["url"] = url
                info["relative_url"] = entry.findtext("relative-url", "")
                repo = entry.find("repository")
                if repo is not None:
                    info["repo_root"] = repo.findtext("root", "")
                # 从 commit 子元素获取 Last Changed Rev
                commit_el = entry.find("commit")
                if commit_el is not None:
                    rev = commit_el.get("revision", "")
                    info["commit"] = f"r{rev}"
                    info["commit_short"] = f"r{rev}"
                    info["last_author"] = commit_el.findtext("author", "")
                # 从 URL 提取分支名
                if url:
                    for marker in ["/branches/", "/tags/"]:
                        if marker in url:
                            info["branch"] = url.split(marker, 1)[1].split("/")[0]
                            break
                    else:
                        if "/trunk" in url:
                            info["branch"] = "trunk"
                        else:
                            info["branch"] = url.rstrip("/").rsplit("/", 1)[-1]
        except ET.ParseError as e:
            logger.warning(f"svn info XML 解析失败: {e}")
    else:
        logger.warning(f"svn info 失败: {err}")

    # ── svn log --xml -l 1 → 最新提交信息 ─────────
    rc, out, _ = await _run_svn(["svn", "log", "--xml", "-l", "1"], workspace_dir, timeout=30)
    if rc == 0 and out.strip():
        try:
            root = ET.fromstring(out)
            entry = root.find("logentry")
            if entry is not None:
                msg = entry.findtext("msg", "").strip()
                if msg:
                    info["commit_message"] = msg
        except ET.ParseError:
            pass

    return info


async def get_workspace_vcs_info(workspace_dir: str) -> Dict:
    """统一接口: 获取工作区 VCS 信息 (自动检测 Git/SVN)"""
    vcs_type = detect_vcs_type(workspace_dir)

    if vcs_type == "git":
        git_info = await get_workspace_git_info(workspace_dir)
        return {"type": "git", **git_info}
    elif vcs_type == "svn":
        return await get_workspace_svn_info(workspace_dir)
    else:
        return {"type": "none", "branch": "", "commit": "", "commit_short": "", "commit_message": ""}


# ======================== 工作区概览 ========================

def _scan_language_stats(workspace_dir: str) -> List[Dict]:
    """
    扫描工作区文件按语言分组统计 (按语言名合并)
    返回: [{"language": "Python", "count": 42}, ...] 按 count 降序, Top 15
    """
    lang_counter: Counter = Counter()
    file_count = 0
    _MAX_FILES = 500_000  # 超大仓库安全上限
    try:
        for root, dirs, files in os.walk(workspace_dir, topdown=True):
            # 原地修改 dirs 以跳过忽略的目录
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
            for f in files:
                file_count += 1
                if file_count > _MAX_FILES:
                    break
                _, ext = os.path.splitext(f)
                ext = ext.lower()
                lang = _EXT_LANG_MAP.get(ext)
                if lang:
                    lang_counter[lang] += 1
            if file_count > _MAX_FILES:
                logger.warning(f"语言统计: 文件数超过 {_MAX_FILES}, 提前终止扫描")
                break
    except Exception as e:
        logger.warning(f"语言统计扫描失败: {e}")

    result = []
    for lang, count in lang_counter.most_common(15):
        result.append({"language": lang, "count": count})
    return result


def _detect_key_files(workspace_dir: str) -> List[str]:
    """检测工作区根目录中存在的关键文件"""
    found = []
    for fname in _KEY_FILES:
        if os.path.exists(os.path.join(workspace_dir, fname)):
            found.append(fname)
    return found


async def _get_git_contributors(workspace_dir: str, top_n: int = 10) -> List[Dict]:
    """获取 Git 仓库贡献者 (Top N)"""
    rc, out, _ = await _run_git(
        ["git", "shortlog", "-sn", "--no-merges", "HEAD"],
        workspace_dir, timeout=30,
    )
    if rc != 0 or not out.strip():
        return []
    contributors = []
    for line in out.strip().splitlines()[:top_n]:
        line = line.strip()
        parts = line.split("\t", 1)
        if len(parts) == 2:
            contributors.append({"name": parts[1].strip(), "commits": int(parts[0].strip())})
    return contributors


async def _get_svn_contributors(workspace_dir: str, top_n: int = 10) -> List[Dict]:
    """获取 SVN 仓库贡献者 (从最近 500 条日志统计, --xml 输出)"""
    rc, out, _ = await _run_svn(
        ["svn", "log", "-l", "500", "--xml"],
        workspace_dir, timeout=60,
    )
    if rc != 0 or not out.strip():
        return []
    try:
        root = ET.fromstring(out)
        counter: Counter = Counter()
        for entry in root.findall("logentry"):
            author = entry.findtext("author", "").strip()
            counter[author or "(no author)"] += 1
        return [{"name": name, "commits": count} for name, count in counter.most_common(top_n)]
    except ET.ParseError as e:
        logger.warning(f"svn log XML 解析失败 (contributors): {e}")
        return []


async def _get_git_recent_commits(workspace_dir: str, n: int = 10) -> List[Dict]:
    """获取 Git 最近 N 条提交"""
    rc, out, _ = await _run_git(
        ["git", "log", f"--oneline", f"-{n}", "--format=%h|%s|%an|%ar"],
        workspace_dir, timeout=15,
    )
    if rc != 0 or not out.strip():
        return []
    commits = []
    for line in out.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "hash": parts[0], "message": parts[1],
                "author": parts[2], "time_ago": parts[3],
            })
    return commits


async def _get_svn_recent_commits(workspace_dir: str, n: int = 10) -> List[Dict]:
    """获取 SVN 最近 N 条提交 (--xml 输出, 无编码问题)"""
    rc, out, _ = await _run_svn(
        ["svn", "log", "--xml", "-l", str(n)],
        workspace_dir, timeout=30,
    )
    if rc != 0 or not out.strip():
        return []
    try:
        root = ET.fromstring(out)
        commits = []
        for entry in root.findall("logentry"):
            rev = entry.get("revision", "")
            author = entry.findtext("author", "").strip()
            date_str = entry.findtext("date", "").strip()
            msg = entry.findtext("msg", "").strip()
            # ISO 日期 → 可读格式
            time_display = date_str
            if date_str:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    # 转本地时间显示
                    local_dt = dt.astimezone()
                    time_display = local_dt.strftime("%Y-%m-%d %H:%M:%S %z")
                except Exception:
                    pass
            commits.append({
                "hash": f"r{rev}" if rev else "",
                "author": author or "(no author)",
                "time_ago": time_display,
                "message": msg,
            })
        return commits
    except ET.ParseError as e:
        logger.warning(f"svn log XML 解析失败 (recent_commits): {e}")
        return []


async def _get_uncommitted_count(workspace_dir: str, vcs_type: str) -> int:
    """获取未提交变更数"""
    if vcs_type == "git":
        rc, out, _ = await _run_git(["git", "status", "--porcelain"], workspace_dir, timeout=30)
    elif vcs_type == "svn":
        rc, out, _ = await _run_svn(["svn", "status", "--depth=immediates"], workspace_dir, timeout=60)
    else:
        return 0
    if rc != 0:
        return 0
    return len([l for l in out.splitlines() if l.strip()])


async def get_workspace_overview(force_refresh: bool = False) -> Dict:
    """
    获取工作区概览 (聚合 VCS + 语言统计 + 关键文件)
    结果缓存 60 秒 (文件扫描可能较慢)
    """
    global _overview_cache, _overview_cache_ts

    ws = await _resolve_active_workspace_path()
    now = time.time()
    if (
        not force_refresh
        and _overview_cache
        and _overview_cache.get("workspace_path") == ws
        and (now - _overview_cache_ts) < _OVERVIEW_CACHE_TTL
    ):
        return {**_overview_cache, "from_cache": True}

    ws_exists = os.path.isdir(ws)

    result = {
        "workspace_path": ws,
        "workspace_exists": ws_exists,
        "vcs_type": "none",
        "vcs": {"type": "none", "branch": "", "commit": "", "commit_short": "", "commit_message": "",
               "last_commit_hash": "", "last_commit_message": ""},
        "recent_commits": [],
        "contributors": [],
        "language_stats": [],
        "key_files": [],
        "total_files": 0,
        "uncommitted_count": 0,
        "cached_at": now,
    }

    if not ws_exists:
        _overview_cache = result
        _overview_cache_ts = now
        return result

    # VCS 信息 (可能耗时)
    vcs_type = detect_vcs_type(ws)
    result["vcs_type"] = vcs_type
    vcs_info = await get_workspace_vcs_info(ws)
    # 补充快捷字段
    vcs_info["last_commit_hash"] = vcs_info.get("commit", "")
    vcs_info["last_commit_message"] = vcs_info.get("commit_message", "")
    result["vcs"] = vcs_info

    # 并行执行耗时操作
    async def _empty_list():
        return []

    async def _zero():
        return 0

    tasks = []
    # 最近提交
    if vcs_type == "git":
        tasks.append(_get_git_recent_commits(ws, 10))
    elif vcs_type == "svn":
        tasks.append(_get_svn_recent_commits(ws, 10))
    else:
        tasks.append(_empty_list())

    # 贡献者
    if vcs_type == "git":
        tasks.append(_get_git_contributors(ws, 10))
    elif vcs_type == "svn":
        tasks.append(_get_svn_contributors(ws, 10))
    else:
        tasks.append(_empty_list())

    # 未提交变更
    if vcs_type in ("git", "svn"):
        tasks.append(_get_uncommitted_count(ws, vcs_type))
    else:
        tasks.append(_zero())

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        commits_raw = results[0] if not isinstance(results[0], Exception) else []
        # 确保每条 commit 都有 'time' 字段 (前端使用)
        for c in commits_raw:
            if "time" not in c:
                c["time"] = c.get("time_ago", "")
        result["recent_commits"] = commits_raw
        result["contributors"] = results[1] if not isinstance(results[1], Exception) else []
        result["uncommitted_count"] = results[2] if not isinstance(results[2], Exception) else 0
    except Exception as e:
        logger.warning(f"并行获取 VCS 数据失败: {e}")

    # 语言统计 (同步文件扫描, 在线程中执行以避免阻塞)
    try:
        loop = asyncio.get_event_loop()
        raw_langs = await loop.run_in_executor(None, _scan_language_stats, ws)
        total_matched = sum(item["count"] for item in raw_langs) or 1
        result["total_files"] = total_matched
        result["language_stats"] = [
            {"language": item["language"], "count": item["count"],
             "percentage": round(item["count"] * 100 / total_matched, 1)}
            for item in raw_langs
        ]
    except Exception as e:
        logger.warning(f"语言统计失败: {e}")

    # 关键文件检测 (快速)
    try:
        result["key_files"] = _detect_key_files(ws)
    except Exception as e:
        logger.warning(f"关键文件检测失败: {e}")

    _overview_cache = result
    _overview_cache_ts = now
    return result
