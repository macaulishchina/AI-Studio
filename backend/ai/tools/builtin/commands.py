"""
内置工具 — 命令执行

run_command (只读白名单), run_command_unrestricted (写命令, 需审批)
"""
import asyncio
import os
import re as _re
from typing import Any, Dict, Set, Optional

import logging

logger = logging.getLogger(__name__)

# 命令超时
COMMAND_TIMEOUT_SECONDS = 30

# 只读命令白名单: {命令: 允许的子命令集合 (None=全部允许)}
READONLY_COMMANDS: Dict[str, Optional[Set[str]]] = {
    "git": {"log", "diff", "show", "status", "branch", "tag", "describe",
            "rev-parse", "ls-files", "blame", "shortlog", "remote", "stash"},
    "ls": None, "cat": None, "head": None, "tail": None,
    "find": None, "grep": None, "wc": None, "file": None,
    "diff": None, "pwd": None, "echo": None, "which": None,
    "du": None, "stat": None, "realpath": None, "dirname": None,
    "basename": None, "env": None, "uname": None, "whoami": None,
    "date": None, "tree": None, "less": None, "more": None,
    "sort": None, "uniq": None, "awk": None, "sed": None,
    "cut": None, "tr": None, "xargs": None,
    "python3": {"-c", "--version", "-V"},
    "python": {"-c", "--version", "-V"},
    "node": {"-e", "--version", "-v"},
    "docker": {"ps", "images", "logs", "inspect", "stats", "top", "version", "info"},
    "docker-compose": {"ps", "logs", "config", "images"},
}

# 极端危险模式 (任何情况都阻止)
LETHAL_PATTERNS = ["rm -rf /", "mkfs", "> /dev/", ":(){ :|:& };:", "shutdown", "reboot"]

# 输出截断限制
MAX_CMD_OUTPUT = 8000


def is_readonly_command(command_str: str) -> bool:
    """
    检查命令是否为只读命令

    检查层级:
    1. 全局写操作符检测: >, >>, &&, ;, |tee 等
    2. 管道链: 每个子命令都必须在白名单中
    3. 白名单匹配: 命令 + 子命令检查
    """
    stripped = command_str.strip()
    if not stripped:
        return False

    # 检测写操作符
    if _re.search(r'>{1,2}', stripped):
        return False
    if '&&' in stripped or ';' in stripped:
        return False
    if _re.search(r'\|\s*tee\b', stripped):
        return False
    if '`' in stripped or '$(' in stripped:
        return False

    # 管道链检查
    pipe_segments = [s.strip() for s in stripped.split('|') if s.strip()]
    for seg in pipe_segments:
        parts = seg.split()
        if not parts:
            return False
        cmd = os.path.basename(parts[0])

        allowed_subs = READONLY_COMMANDS.get(cmd)
        if allowed_subs is None and cmd in READONLY_COMMANDS:
            continue
        if allowed_subs is not None:
            if len(parts) >= 2 and parts[1] in allowed_subs:
                continue
            elif len(parts) < 2:
                continue
            else:
                return False
        else:
            return False

    return True


def _format_command_output(command: str, stdout: bytes, stderr: bytes, returncode: int) -> str:
    """格式化命令输出"""
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()

    if len(out) > MAX_CMD_OUTPUT:
        out = out[:MAX_CMD_OUTPUT] + f"\n\n... (输出已截断至 {MAX_CMD_OUTPUT} 字符)"

    result = f"$ {command}\n"
    if out:
        result += f"\n{out}"
    if err:
        result += f"\n(stderr) {err}"
    if returncode != 0:
        result += f"\n(exit code: {returncode})"
    return result


async def tool_run_command(args: Dict[str, Any], workspace: str) -> str:
    """执行 shell 命令 (只读白名单)"""
    command = args.get("command", "").strip()
    if not command:
        return "⚠️ 请指定要执行的命令"

    # 安全检查
    for pattern in LETHAL_PATTERNS:
        if pattern in command:
            return f"⚠️ 命令包含危险模式: '{pattern}'，已阻止执行"

    if not is_readonly_command(command):
        return (
            f"⚠️ 此命令不在只读白名单中，需要 '执行任意命令' 权限。\n"
            f"命令: {command}\n\n"
            f"只读命令示例: git log, git diff, ls, cat, grep, find, python3 -c 等\n"
            f"如需执行此命令，请让项目管理员开启 'execute_command' 权限。"
        )

    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=COMMAND_TIMEOUT_SECONDS
        )
        return _format_command_output(command, stdout, stderr, proc.returncode)
    except asyncio.TimeoutError:
        return f"⚠️ 命令执行超时 ({COMMAND_TIMEOUT_SECONDS}s): {command}"
    except Exception as e:
        return f"⚠️ 命令执行失败: {str(e)}"


async def tool_run_command_unrestricted(args: Dict[str, Any], workspace: str) -> str:
    """执行任意命令 (需要 execute_command 权限 + 审批)"""
    command = args.get("command", "").strip()
    if not command:
        return "⚠️ 请指定要执行的命令"

    for pattern in LETHAL_PATTERNS:
        if pattern in command:
            return f"⚠️ 命令包含极端危险模式: '{pattern}'，已阻止执行"

    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=COMMAND_TIMEOUT_SECONDS * 2
        )
        return _format_command_output(command, stdout, stderr, proc.returncode)
    except asyncio.TimeoutError:
        return f"⚠️ 命令执行超时 ({COMMAND_TIMEOUT_SECONDS * 2}s): {command}"
    except Exception as e:
        return f"⚠️ 命令执行失败: {str(e)}"
