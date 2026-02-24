"""
设计院 (Studio) - 上下文管理器

负责将对话消息 + system prompt 控制在模型上下文窗口内。

上下文预算分配:
  ├── System Prompt       ~15%  (可压缩)
  ├── Tool Definitions    ~5%   (如启用)
  ├── Plan Summary        ~5%   (当前 plan 摘要)
  ├── 对话历史            ~60%  (旧消息优先移除)
  ├── 用户最新消息        ~10%
  └── Output Reserve      ~5%   (安全裕量)

消息截断策略 (参考 VS Code Copilot):
  1. 始终保留: system prompt + 最新 N 条消息 (至少最新 1 轮)
  2. 从最旧消息开始逐条移除
  3. 特别长的工具结果先截断
"""
import logging
from typing import List, Dict, Any, Optional, Tuple

from studio.backend.core.model_capabilities import capability_cache
from studio.backend.core.token_utils import estimate_tokens, estimate_messages_tokens, truncate_text

logger = logging.getLogger(__name__)

# 最少保留的最近消息轮数 (1轮 = 1 user + 1 assistant)
MIN_RECENT_MESSAGES = 2

# 输出预留 token 比例
OUTPUT_RESERVE_RATIO = 0.05

# 安全裕量 (防止估算偏差)
SAFETY_MARGIN = 200

# 上下文总结触发阈值 (使用率超过此值触发总结)
SUMMARY_TRIGGER_RATIO = 0.90

# 总结后目标使用率 (总结将旧消息压缩到这个比例以下)
SUMMARY_TARGET_RATIO = 0.50


def prepare_context(
    messages: List[Dict[str, Any]],
    system_prompt: str,
    model: str,
    plan_summary: str = "",
    tool_definitions: Optional[List[dict]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    根据模型上下文窗口,智能管理对话上下文

    Args:
        messages: 原始消息列表 [{"role":"user","content":"..."}, ...]
        system_prompt: 系统 prompt
        model: 模型ID (可含 copilot: 前缀)
        plan_summary: 当前 plan 摘要 (注入 system prompt)
        tool_definitions: 工具定义列表 (OpenAI format)

    Returns:
        (managed_messages, usage_info)
        managed_messages: 截断后的消息列表
        usage_info: {
            "max_input": 总上下文窗口,
            "system_tokens": system prompt tokens,
            "tools_tokens": 工具定义 tokens,
            "history_tokens": 保留的历史 tokens,
            "total_tokens": 总使用 tokens,
            "messages_kept": 保留的消息数,
            "messages_dropped": 丢弃的消息数,
        }
    """
    max_input, max_output = capability_cache.get_context_window(model)

    # 可用输入 token = 总窗口 - 输出预留 - 安全裕量
    output_reserve = int(max_output * OUTPUT_RESERVE_RATIO) + SAFETY_MARGIN
    available = max_input - output_reserve

    if available <= 0:
        available = max(max_input // 2, 4000)  # 至少留一半

    # 1. 计算固定部分: system prompt
    system_tokens = estimate_tokens(system_prompt) if system_prompt else 0

    # 2. 计算 plan 摘要 (嵌入 system prompt 中)
    plan_tokens = estimate_tokens(plan_summary) if plan_summary else 0

    # 3. 计算工具定义
    tools_tokens = 0
    if tool_definitions:
        import json
        tools_str = json.dumps(tool_definitions, ensure_ascii=False)
        tools_tokens = estimate_tokens(tools_str)

    # 固定占用
    fixed_tokens = system_tokens + plan_tokens + tools_tokens

    # 对话历史可用的 token
    history_budget = available - fixed_tokens

    if history_budget < 500:
        # 上下文极小 → 压缩 system prompt
        logger.warning(
            f"模型 {model} 上下文太小 (max_input={max_input}),"
            f"固定占用 {fixed_tokens} tokens, 尝试压缩"
        )
        # 保留最基础的 system prompt
        history_budget = max(available // 2, 2000)

    # 4. 消息截断
    managed, kept, dropped = _truncate_messages(messages, history_budget)

    # 计算实际占用
    history_tokens = estimate_messages_tokens(managed)
    total_tokens = fixed_tokens + history_tokens

    usage_info = {
        "max_input": max_input,
        "max_output": max_output,
        "system_tokens": system_tokens + plan_tokens,
        "tools_tokens": tools_tokens,
        "history_tokens": history_tokens,
        "total_tokens": total_tokens,
        "available_tokens": available,
        "messages_kept": kept,
        "messages_dropped": dropped,
        "messages_total": len(messages),
    }

    logger.info(
        f"上下文管理 [{model}]: 窗口={max_input}, "
        f"使用={total_tokens}/{available} ({total_tokens*100//max(available,1)}%), "
        f"消息 {kept}/{len(messages)} 条保留"
    )

    return managed, usage_info


def _truncate_messages(
    messages: List[Dict[str, Any]],
    budget: int,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    在 token 预算内截断消息列表

    策略:
      1. 从最旧的消息开始移除
      2. 始终保留最近 MIN_RECENT_MESSAGES 轮
      3. 特别长的单条消息 (> budget * 30%) 截断其内容
    """
    if not messages:
        return [], 0, 0

    total_tokens = estimate_messages_tokens(messages)

    if total_tokens <= budget:
        return list(messages), len(messages), 0

    # 保护最新的 N 条消息 (至少 MIN_RECENT_MESSAGES * 2)
    protected_count = min(len(messages), MIN_RECENT_MESSAGES * 2)
    protected = messages[-protected_count:]
    droppable = messages[:-protected_count]

    # 从最旧的消息开始逐条移除
    dropped = 0
    while droppable and estimate_messages_tokens(droppable + protected) > budget:
        droppable.pop(0)
        dropped += 1

    result = droppable + protected

    # 如果仍然超预算, 截断最长的单条消息内容
    single_limit = max(budget // 3, 1000)
    for msg in result:
        content = msg.get("content", "")
        if isinstance(content, str) and estimate_tokens(content) > single_limit:
            msg = dict(msg)  # 不修改原消息
            msg["content"] = truncate_text(content, single_limit)

    # 最终检查: 如果还是超了, 只保留最后几条
    if estimate_messages_tokens(result) > budget:
        while len(result) > 2 and estimate_messages_tokens(result) > budget:
            result.pop(0)
            dropped += 1

    kept = len(result)
    return result, kept, dropped


async def summarize_context_if_needed(
    messages: List[Dict[str, Any]],
    system_prompt: str,
    model: str,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    检查上下文使用率，超过 90% 时自动总结旧消息

    工作流:
      1. 预估当前上下文使用情况
      2. 若使用率 > 90%, 将被截断的旧消息送去 AI 总结
      3. 用总结替换旧消息, 插入为 system 消息
      4. 返回新的消息列表 + 总结文本 (None = 未触发)

    Args:
        messages: 原始消息列表 (DB 中的全部历史)
        system_prompt: 系统 prompt
        model: 当前模型

    Returns:
        (new_messages, summary_text)
        - new_messages: 可能包含总结消息的新列表
        - summary_text: 总结文本 (None 表示未触发总结)
    """
    max_input, _ = capability_cache.get_context_window(model)
    output_reserve = int(capability_cache.get_max_output(model) * OUTPUT_RESERVE_RATIO) + SAFETY_MARGIN
    available = max_input - output_reserve
    if available <= 0:
        available = max(max_input // 2, 4000)

    # 计算当前占用 (system + 全部历史)
    system_tokens = estimate_tokens(system_prompt) if system_prompt else 0
    history_tokens = estimate_messages_tokens(messages)
    total_tokens = system_tokens + history_tokens

    usage_ratio = total_tokens / max(available, 1)

    if usage_ratio < SUMMARY_TRIGGER_RATIO:
        # 使用率未达阈值, 无需总结
        return messages, None

    logger.info(
        f"上下文总结触发: [{model}] 使用率 {usage_ratio:.0%} > {SUMMARY_TRIGGER_RATIO:.0%}, "
        f"总 {len(messages)} 条消息, {total_tokens}/{available} tokens"
    )

    # 确定需要总结的消息范围: 保留最近 N 条, 其余送去总结
    # 目标: 总结后 system + summary + recent < available * TARGET_RATIO
    target_tokens = int(available * SUMMARY_TARGET_RATIO)
    recent_budget = target_tokens - system_tokens

    # 至少保留最近 4 条消息 (2 轮对话)
    min_keep = min(len(messages), MIN_RECENT_MESSAGES * 2)
    recent_messages = messages[-min_keep:]
    to_summarize = messages[:-min_keep] if min_keep < len(messages) else []

    # 如果保留的消息已经超预算，增加截断
    while len(recent_messages) > 2 and estimate_messages_tokens(recent_messages) > recent_budget:
        # 将最旧的 recent 移到待总结区
        to_summarize.append(recent_messages.pop(0))

    if not to_summarize:
        # 没有可总结的旧消息
        return messages, None

    # 构建总结 prompt
    summary_text = await _generate_summary(to_summarize, model)

    if not summary_text:
        # 总结失败, 回退到原始消息
        return messages, None

    # 构建新消息列表: [总结消息] + 保留的最近消息
    summary_msg = {
        "role": "system",
        "content": f"[对话历史总结 - 以下是之前 {len(to_summarize)} 条消息的摘要]\n\n{summary_text}",
    }

    new_messages = [summary_msg] + recent_messages

    new_tokens = estimate_messages_tokens(new_messages)
    logger.info(
        f"上下文总结完成: {len(to_summarize)} 条旧消息 → 1 条总结 "
        f"({history_tokens} → {new_tokens} tokens, "
        f"节省 {history_tokens - new_tokens} tokens)"
    )

    return new_messages, summary_text


async def _generate_summary(
    messages: List[Dict[str, Any]],
    model: str,
) -> Optional[str]:
    """
    调用 AI 对旧消息生成总结

    使用同一个模型 (但限制 max_tokens 以快速完成)
    """
    # 构建待总结的对话文本
    conversation_parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            # 截断特别长的单条消息
            if len(content) > 2000:
                content = content[:2000] + "...(截断)"
            conversation_parts.append(f"[{role}]: {content}")

    conversation_text = "\n\n".join(conversation_parts)

    # 限制送去总结的文本量 (最多 ~4000 tokens 估算)
    if estimate_tokens(conversation_text) > 6000:
        conversation_text = truncate_text(conversation_text, 6000)

    summary_prompt = f"""请简洁地总结以下对话的关键内容。要求：
1. 保留所有重要的技术决策和结论
2. 保留提到的具体文件名、API 端点、数据结构
3. 保留尚未解决的问题和待办事项
4. 使用中文
5. 控制在 300 字以内

对话内容:
{conversation_text}

请直接输出总结（不需要标题或格式）:"""

    try:
        from studio.backend.services.ai_service import chat_complete
        summary = await chat_complete(
            messages=[{"role": "user", "content": summary_prompt}],
            model=model,
            system_prompt="你是一个对话总结助手。请简洁准确地总结对话要点。",
            max_tokens=800,
        )
        if summary and not summary.startswith("❌"):
            return summary.strip()
        else:
            logger.warning(f"上下文总结失败: {summary}")
            return None
    except Exception as e:
        logger.exception(f"上下文总结异常: {e}")
        return None


def build_usage_summary(usage_info: dict, system_sections: list = None, history_messages: list = None) -> dict:
    """
    构建前端可用的上下文使用情况摘要 (用于饼图/进度条/树形检查器)

    Args:
        usage_info: 上下文管理器返回的使用信息
        system_sections: system prompt 分段明细 (来自 build_project_context)
        history_messages: 保留的对话消息列表 (用于每条消息的 token 明细)

    Returns:
        {
            "total": 总容量,
            "used": 已使用,
            "available": 剩余可用,
            "percentage": 使用百分比,
            "breakdown": {
                "system": system prompt 占用,
                "tools": 工具定义占用,
                "history": 对话历史占用,
            },
            "system_sections": [{"name": ..., "tokens": ..., "children": [...]}],
            "history_detail": [{"role": ..., "tokens": ..., "preview": ...}],
            "messages": {
                "kept": 保留消息数,
                "dropped": 丢弃消息数,
                "total": 总消息数,
            },
        }
    """
    max_input = usage_info.get("max_input", 0)
    total_used = usage_info.get("total_tokens", 0)
    available = max(0, max_input - total_used)
    percentage = min(100, total_used * 100 // max(max_input, 1))

    result = {
        "total": max_input,
        "used": total_used,
        "available": available,
        "percentage": percentage,
        "breakdown": {
            "system": usage_info.get("system_tokens", 0),
            "tools": usage_info.get("tools_tokens", 0),
            "history": usage_info.get("history_tokens", 0),
        },
        "messages": {
            "kept": usage_info.get("messages_kept", 0),
            "dropped": usage_info.get("messages_dropped", 0),
            "total": usage_info.get("messages_total", 0),
        },
    }

    # 添加 system prompt 分段明细 (保留 content 用于前端查看，截断过长内容)
    if system_sections:
        MAX_CONTENT = 5000  # 单段最大字符数

        def _slim_section(sec):
            content = sec.get("content", "")
            out = {
                "name": sec["name"],
                "tokens": sec["tokens"],
                "content": content[:MAX_CONTENT] + ("…" if len(content) > MAX_CONTENT else ""),
            }
            if sec.get("children"):
                out["children"] = [_slim_section(c) for c in sec["children"]]
            return out
        result["system_sections"] = [_slim_section(s) for s in system_sections]

    # 添加对话历史每条消息的 token 占用
    if history_messages:
        detail = []
        for msg in history_messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                # 多模态消息
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content_text = " ".join(text_parts)
            else:
                content_text = str(content)
            tokens = estimate_tokens(content_text)
            preview = content_text[:60].replace("\n", " ") if content_text else ""
            detail.append({
                "role": msg.get("role", "?"),
                "tokens": tokens,
                "preview": preview,
            })
        result["history_detail"] = detail

    return result
