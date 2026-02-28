"""
ReAct Agent — 增强版 Tool-Calling Agent

将原 ai_service.chat_stream() 中的 tool loop 提取为正式 Agent:
  - 多轮工具调用循环
  - 伪造检测 (Fabrication Guard)
  - 重复调用去重
  - 结果截断 (适配上下文窗口)
  - 可选反思 (Reflection)
  - 并行工具执行支持

事件输出完全兼容现有 SSE 协议。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from .base import (
    AgentConfig,
    AgentEvent,
    AgentEventType,
    AgentInput,
    AgentPlan,
    BaseAgent,
    ReflectionResult,
)

logger = logging.getLogger(__name__)


class ReActAgent(BaseAgent):
    """
    ReAct (Reasoning + Acting) Agent

    核心逻辑:
      1. 发送消息给 LLM (via LLMClient.stream)
      2. 如果 LLM 返回 tool calls → 执行工具 → 将结果注入消息 → 回到 1
      3. 如果 LLM 返回纯文本 → 检测伪造 → 输出
      4. 每 N 轮可选 reflection
    """

    async def act(
        self, input: AgentInput, plan: Optional[AgentPlan] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """主执行循环 — 委托给 LLMClient + tool loop"""
        from studio.backend.ai.llm import get_llm_client, COPILOT_PREFIX, _is_reasoning_model
        from studio.backend.core.model_capabilities import capability_cache
        from studio.backend.core.token_utils import estimate_tokens, estimate_messages_tokens, truncate_text

        client = get_llm_client()

        model = input.model
        tools = input.tools
        tool_executor = input.tool_executor

        # 推理模型禁用 tools
        actual_model = model.removeprefix(COPILOT_PREFIX)
        if _is_reasoning_model(actual_model) and tools:
            logger.info(f"推理模型 {actual_model} 不支持 tools, 跳过工具注入")
            tools = None

        current_messages = list(input.messages)
        total_tool_rounds = 0
        seen_tool_calls: Set[str] = set()
        all_tool_calls: list = []
        fabrication_retries = 0

        while True:
            # 流式收集
            pending_tool_calls: Dict[int, Dict[str, Any]] = {}
            started_tool_calls: set = set()
            response_has_content = False
            stream_finish_reason = None
            response_text_parts: list = []
            usage_data = None

            # tool_choice
            current_tool_choice = "auto"
            if fabrication_retries > 0:
                current_tool_choice = "required"
                logger.info(f"伪造重试中, 强制 tool_choice=required (retry={fabrication_retries})")

            try:
                async for event in client.stream(
                    current_messages, model,
                    system_prompt=input.system_prompt,
                    temperature=input.temperature,
                    max_tokens=input.max_tokens,
                    tools=tools,
                    tool_choice=current_tool_choice,
                    request_id=input.request_id,
                ):
                    evt_type = event.type
                    data = event.data

                    if evt_type == "content":
                        yield AgentEvent(type=AgentEventType.CONTENT, data={"content": data["content"]})
                        response_has_content = True
                        response_text_parts.append(data["content"])

                    elif evt_type == "thinking":
                        yield AgentEvent(type=AgentEventType.THINKING, data={"content": data["content"]})

                    elif evt_type == "tool_call_delta":
                        idx = data["tool_call_index"]
                        if idx not in pending_tool_calls:
                            pending_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                        tc = pending_tool_calls[idx]
                        if data.get("tool_call_id"):
                            tc["id"] = data["tool_call_id"]
                        if data.get("name"):
                            tc["name"] = data["name"]
                            if data["name"] == "ask_user" and idx not in started_tool_calls and tc["id"]:
                                started_tool_calls.add(idx)
                                yield AgentEvent(
                                    type=AgentEventType.TOOL_CALL_START,
                                    data={"tool_call": {"id": tc["id"], "name": "ask_user"}},
                                )
                        if data.get("arguments_delta"):
                            tc["arguments"] += data["arguments_delta"]

                    elif evt_type == "usage":
                        usage_data = data.get("usage", {})

                    elif evt_type == "finish":
                        stream_finish_reason = data.get("finish_reason", "")

                    elif evt_type == "error":
                        error_meta = data.get("error_meta", {})
                        if error_meta:
                            capability_cache.learn_from_error(model, data.get("error", ""))
                        yield AgentEvent(
                            type=AgentEventType.ERROR,
                            data={"error": data.get("error", ""), "error_meta": error_meta},
                        )
                        return

            except Exception as e:
                logger.exception("Agent stream error")
                yield AgentEvent(type=AgentEventType.ERROR, data={"error": f"❌ AI 服务异常: {str(e)}"})
                return

            # Usage
            if usage_data:
                yield AgentEvent(type=AgentEventType.USAGE, data={
                    "usage": {
                        "prompt_tokens": usage_data.get("prompt_tokens", 0),
                        "completion_tokens": usage_data.get("completion_tokens", 0),
                        "total_tokens": usage_data.get("total_tokens", 0),
                        "tool_rounds": total_tool_rounds,
                    }
                })

            # 截断检测
            if stream_finish_reason == "length":
                if pending_tool_calls:
                    logger.info(f"输出因 max_tokens 截断, 丢弃 {len(pending_tool_calls)} 个不完整工具调用")
                    pending_tool_calls.clear()
                if response_has_content:
                    yield AgentEvent(type=AgentEventType.TRUNCATED, data={})

            # 处理 tool calls
            if pending_tool_calls and tool_executor:
                total_tool_rounds += 1
                if total_tool_rounds > input.max_tool_rounds:
                    yield AgentEvent(
                        type=AgentEventType.CONTENT,
                        data={"content": f"\n\n⚠️ 工具调用已达上限 ({input.max_tool_rounds}轮)，停止继续调用。"},
                    )
                    return

                sorted_tcs = sorted(pending_tool_calls.items())
                tool_results_messages: list = []

                # assistant tool_calls 消息
                assistant_tool_calls = []
                for _, tc in sorted_tcs:
                    assistant_tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    })
                current_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": assistant_tool_calls,
                })

                for _, tc in sorted_tcs:
                    try:
                        arguments = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        arguments = {"_raw": tc["arguments"]}

                    # 重复检测
                    call_sig = f"{tc['name']}:{json.dumps(arguments, sort_keys=True)}"
                    is_duplicate = call_sig in seen_tool_calls
                    seen_tool_calls.add(call_sig)

                    yield AgentEvent(
                        type=AgentEventType.TOOL_CALL,
                        data={"tool_call": {"id": tc["id"], "name": tc["name"], "arguments": arguments}},
                    )

                    if is_duplicate:
                        result_text = "⚠️ 你已经读取过这个内容了，请直接使用之前的结果，不要重复读取。"
                        yield AgentEvent(
                            type=AgentEventType.TOOL_RESULT,
                            data={
                                "tool_call_id": tc["id"], "name": tc["name"],
                                "arguments": arguments, "result": result_text, "duration_ms": 0,
                            },
                        )
                        tool_results_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})
                        continue

                    # 执行工具
                    start_time = time.monotonic()
                    try:
                        result_text = await tool_executor(tc["name"], arguments)
                        duration_ms = int((time.monotonic() - start_time) * 1000)

                        # 截断适配
                        max_input, _ = capability_cache.get_context_window(model)
                        current_tokens = estimate_messages_tokens(current_messages)
                        result_tokens = estimate_tokens(result_text)
                        remaining_budget = max_input - current_tokens - input.max_tokens - 200

                        if result_tokens > remaining_budget and remaining_budget > 500:
                            result_text = truncate_text(result_text, remaining_budget)
                            result_text += f"\n\n[… 内容已截断以适配模型上下文窗口 ({remaining_budget} tokens), 请用 start_line/end_line 指定范围精确读取]"
                        elif remaining_budget <= 500:
                            result_text = truncate_text(result_text, 500)
                            result_text += "\n\n[⚠️ 上下文空间不足, 内容已大幅截断]"

                        yield AgentEvent(
                            type=AgentEventType.TOOL_RESULT,
                            data={
                                "tool_call_id": tc["id"], "name": tc["name"],
                                "arguments": arguments, "result": result_text, "duration_ms": duration_ms,
                            },
                        )
                        all_tool_calls.append({
                            "id": tc["id"], "name": tc["name"],
                            "arguments": arguments, "result": result_text, "duration_ms": duration_ms,
                        })
                        tool_results_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})

                    except Exception as e:
                        duration_ms = int((time.monotonic() - start_time) * 1000)
                        error_msg = f"工具执行失败: {str(e)}"
                        yield AgentEvent(
                            type=AgentEventType.TOOL_ERROR,
                            data={"tool_call_id": tc["id"], "name": tc["name"], "error": error_msg},
                        )
                        all_tool_calls.append({
                            "id": tc["id"], "name": tc["name"],
                            "arguments": arguments, "result": f"ERROR: {error_msg}", "duration_ms": duration_ms,
                        })
                        tool_results_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": error_msg})

                current_messages.extend(tool_results_messages)

                # ask_user 中断
                has_ask_user = any(tc["name"] == "ask_user" for _, tc in sorted_tcs)
                if has_ask_user:
                    yield AgentEvent(type=AgentEventType.ASK_USER_PENDING, data={})
                    return

                # Reflection (可选)
                if (input.enable_reflection
                    and total_tool_rounds % input.reflection_interval == 0
                    and total_tool_rounds > 0):
                    reflection = await self.reflect(input, total_tool_rounds, {
                        "tool_calls_count": len(all_tool_calls),
                        "seen_duplicates": len(seen_tool_calls),
                    })
                    if reflection:
                        yield AgentEvent(
                            type=AgentEventType.REFLECTION,
                            data={
                                "reflection": reflection.summary,
                                "action": reflection.action,
                            },
                        )
                        if reflection.action == "abort":
                            yield AgentEvent(
                                type=AgentEventType.CONTENT,
                                data={"content": f"\n\n⚠️ Agent 反思后决定终止: {reflection.summary}"},
                            )
                            return

                continue  # 返回 while 循环

            else:
                # 无 tool calls — 伪造检测
                if response_has_content and tools and fabrication_retries < 2:
                    from studio.backend.services.ai_service import _detect_fabrication
                    try:
                        from studio.backend.api.command_auth import is_fabrication_detection_enabled
                        fabrication_enabled = is_fabrication_detection_enabled()
                    except Exception:
                        fabrication_enabled = True

                    if fabrication_enabled:
                        full_text = "".join(response_text_parts)
                        if _detect_fabrication(full_text):
                            fabrication_retries += 1
                            logger.warning(f"检测到伪造工具执行结果, 重试 (retry={fabrication_retries})")
                            current_messages.append({"role": "assistant", "content": full_text})
                            current_messages.append({
                                "role": "user",
                                "content": (
                                    "⚠️ 你刚才在文本中伪造了命令执行结果，这是严重违规！"
                                    "你并没有真正执行任何命令。"
                                    "请立即通过 tool_call 调用 run_command 工具来执行命令，"
                                    "不要再在文本中编造结果。"
                                ),
                            })
                            yield AgentEvent(
                                type=AgentEventType.CONTENT,
                                data={"content": "\n\n⚠️ 检测到 AI 伪造执行结果，正在重新要求执行...\n\n"},
                            )
                            continue

                if not response_has_content:
                    logger.warning(f"模型返回空响应 (finish_reason={stream_finish_reason})")
                    yield AgentEvent(
                        type=AgentEventType.CONTENT,
                        data={"content": "\n\n⚠️ AI 返回了空响应，请重新发送或换个说法试试。"},
                    )
                return
