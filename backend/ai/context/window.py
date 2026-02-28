"""
上下文窗口管理

从 context_manager.py 迁入:
  - prepare_context(): 消息截断适配模型窗口
  - summarize_context_if_needed(): 自动摘要长对话
  - build_usage_summary(): 前端用量展示数据
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from studio.backend.core.model_capabilities import capability_cache
from studio.backend.core.token_utils import (
    estimate_tokens,
    estimate_messages_tokens,
    truncate_text,
)

logger = logging.getLogger(__name__)

# 常量
MIN_RECENT_MESSAGES = 2
OUTPUT_RESERVE_RATIO = 0.05
SAFETY_MARGIN = 200
SUMMARY_TRIGGER_RATIO = 0.90
SUMMARY_TARGET_RATIO = 0.50


def prepare_context(
    messages: List[Dict[str, Any]],
    system_prompt: str,
    model: str,
    plan_summary: str = "",
    tool_definitions: Optional[List[Dict]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    裁剪消息列表以适配模型上下文窗口

    Returns:
        (managed_messages, usage_info)
    """
    max_input, max_output = capability_cache.get_context_window(model)
    output_reserve = int(max_output * OUTPUT_RESERVE_RATIO) if max_output else 400
    available = max_input - output_reserve - SAFETY_MARGIN

    # 固定开销
    system_tokens = estimate_tokens(system_prompt)
    plan_tokens = estimate_tokens(plan_summary) if plan_summary else 0
    tools_tokens = 0
    if tool_definitions:
        import json
        tools_tokens = estimate_tokens(json.dumps(tool_definitions))

    fixed_cost = system_tokens + plan_tokens + tools_tokens
    history_budget = max(available - fixed_cost, 500)

    # 截断消息
    managed, kept, dropped = _truncate_messages(messages, history_budget)
    history_tokens = estimate_messages_tokens(managed)

    usage_info = {
        "max_input": max_input,
        "max_output": max_output,
        "system_tokens": system_tokens,
        "plan_tokens": plan_tokens,
        "tools_tokens": tools_tokens,
        "history_tokens": history_tokens,
        "history_budget": history_budget,
        "total_used": fixed_cost + history_tokens,
        "available": available,
        "kept_messages": kept,
        "dropped_messages": dropped,
    }

    return managed, usage_info


def _truncate_messages(
    messages: List[Dict[str, Any]],
    budget: int,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """截断消息以适配预算"""
    if not messages:
        return [], 0, 0

    total = estimate_messages_tokens(messages)
    if total <= budget:
        return list(messages), len(messages), 0

    # 保护最近 N 条消息
    protected = min(MIN_RECENT_MESSAGES * 2, len(messages))
    recent = messages[-protected:]
    older = messages[:-protected]

    # 尝试单条大消息截断
    for i, msg in enumerate(recent):
        content = msg.get("content", "")
        if content and estimate_tokens(content) > budget * 0.3:
            trimmed = truncate_text(content, int(budget * 0.3))
            recent[i] = {**msg, "content": trimmed}

    recent_tokens = estimate_messages_tokens(recent)
    remaining_budget = budget - recent_tokens

    if remaining_budget <= 0:
        return recent[-2:], 2, len(messages) - 2

    # 从最旧开始丢弃
    kept_older = []
    for msg in reversed(older):
        msg_tokens = estimate_tokens(msg.get("content", "") or "")
        if remaining_budget >= msg_tokens:
            kept_older.insert(0, msg)
            remaining_budget -= msg_tokens
        else:
            break

    result = kept_older + recent
    dropped = len(messages) - len(result)
    return result, len(result), dropped


async def summarize_context_if_needed(
    messages: List[Dict[str, Any]],
    system_prompt: str,
    model: str,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    检查上下文使用率，超过阈值时自动摘要压缩

    Returns:
        (new_messages, summary_text_or_None)
    """
    max_input, _ = capability_cache.get_context_window(model)
    current_tokens = estimate_messages_tokens(messages) + estimate_tokens(system_prompt)
    usage_ratio = current_tokens / max(max_input, 1)

    if usage_ratio < SUMMARY_TRIGGER_RATIO:
        return messages, None

    # 分割: 保留最近消息, 其余摘要
    keep_count = max(MIN_RECENT_MESSAGES * 2, 4)
    if len(messages) <= keep_count:
        return messages, None

    to_summarize = messages[:-keep_count]
    to_keep = messages[-keep_count:]

    summary = await _generate_summary(to_summarize, model)
    if not summary:
        return messages, None

    summary_msg = {
        "role": "system",
        "content": f"[上下文摘要] 以下是之前对话的关键信息摘要:\n{summary}",
    }
    return [summary_msg] + to_keep, summary


async def _generate_summary(
    messages: List[Dict[str, Any]],
    model: str,
) -> Optional[str]:
    """用 AI 生成对话摘要"""
    try:
        from studio.backend.ai.llm import get_llm_client

        # 准备摘要材料
        text_parts = []
        total_chars = 0
        MAX_CHARS = 12000
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "") or ""
            if len(content) > 2000:
                content = content[:2000] + "..."
            text_parts.append(f"[{role}]: {content}")
            total_chars += len(content)
            if total_chars > MAX_CHARS:
                break

        material = "\n\n".join(text_parts)

        summary_prompt = (
            "请用中文简洁总结以下对话的关键信息 (不超过 300 字)。"
            "重点保留: 做了什么决定、涉及哪些文件/技术选择、未解决的问题。\n\n"
            f"{material}"
        )

        client = get_llm_client()
        result = await client.complete(
            [{"role": "user", "content": summary_prompt}],
            model=model,
            max_tokens=500,
            temperature=0.3,
        )
        return result.strip() if result else None

    except Exception as e:
        logger.warning(f"生成上下文摘要失败: {e}")
        return None


def build_usage_summary(
    usage_info: Dict[str, int],
    system_sections: Optional[List[Dict]] = None,
    history_messages: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """构建前端用量展示数据"""
    total = usage_info.get("total_used", 0)
    available = usage_info.get("available", 1)
    percentage = min(100, int(total / max(available, 1) * 100))

    result = {
        "percentage": percentage,
        "total_tokens": total,
        "available_tokens": available,
        "breakdown": {
            "system": usage_info.get("system_tokens", 0),
            "tools": usage_info.get("tools_tokens", 0),
            "plan": usage_info.get("plan_tokens", 0),
            "history": usage_info.get("history_tokens", 0),
        },
        "messages": {
            "kept": usage_info.get("kept_messages", 0),
            "dropped": usage_info.get("dropped_messages", 0),
        },
    }

    if system_sections:
        result["system_sections"] = [
            {
                "name": s.get("name", ""),
                "tokens": s.get("tokens", 0),
                "content": (s.get("content", "") or "")[:5000],
            }
            for s in system_sections
        ]

    if history_messages:
        msg_details = []
        for msg in history_messages[-20:]:
            content = msg.get("content", "") or ""
            msg_details.append({
                "role": msg.get("role", ""),
                "tokens": estimate_tokens(content),
                "preview": content[:200],
            })
        result["message_details"] = msg_details

    return result
