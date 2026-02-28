"""
内置工具 — 用户交互

ask_user: 向用户提出澄清问题
"""
from typing import Any, Dict


async def tool_ask_user(args: Dict[str, Any], workspace: str) -> str:
    """向用户提出需求澄清问题 (结果直接透传给前端渲染)"""
    questions = args.get("questions", [])
    if not questions:
        return "⚠️ 请至少提出一个问题"
    count = len(questions)
    return f"✅ 已向用户展示 {count} 个问题，请等待用户回答后再继续讨论。不要自行假设答案。"
