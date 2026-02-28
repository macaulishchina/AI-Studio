"""
设计院 (Studio) - AI 对话服务 (兼容层)

本模块保留原有公开 API 签名以向后兼容:
  - chat_stream()           → 委托给 LLMClient + tool-calling loop
  - chat_complete()         → 委托给 LLMClient.complete()
  - new_request_id()        → 委托给 llm.new_request_id()
  - invalidate_provider_cache() → 委托给 LLMClient.invalidate_cache()
  - encode_image_to_base64()
  - get_mime_type()

实际实现已迁至:
  - backend/ai/providers/   → Provider 抽象 (协议差异)
  - backend/ai/llm.py       → LLMClient (统一接口)

SSE 事件协议 (chat_stream yield 结构化 dict):
  {"type": "content",    "content": "..."}            - 文本内容
  {"type": "thinking",   "content": "..."}            - 推理过程 (reasoning models)
  {"type": "tool_call",  "tool_call": {...}}          - AI 请求调用工具
  {"type": "tool_result", "tool_call_id": "...", ...} - 工具执行结果
  {"type": "tool_error", "tool_call_id": "...", ...}  - 工具执行失败
  {"type": "usage",      "usage": {...}}              - token 使用统计
  {"type": "error",      "error": "..."}              - 错误信息
"""
import json
import logging
import re as _re
import time
from typing import List, Dict, Any, Optional, AsyncGenerator, Callable, Awaitable

from studio.backend.ai.llm import (
    LLMClient,
    LLMEvent,
    get_llm_client,
    new_request_id,
    encode_image_to_base64,
    get_mime_type,
    _is_reasoning_model,
    COPILOT_PREFIX,
)
from studio.backend.core.model_capabilities import capability_cache
from studio.backend.core.token_utils import estimate_tokens, estimate_messages_tokens, truncate_text

logger = logging.getLogger(__name__)

# Re-export
__all__ = [
    "chat_stream",
    "chat_complete",
    "new_request_id",
    "invalidate_provider_cache",
    "encode_image_to_base64",
    "get_mime_type",
    "MAX_TOOL_ROUNDS",
    "ToolExecutor",
]

# 工具调用最大循环次数
MAX_TOOL_ROUNDS = 15

# Tool call 执行回调类型
ToolExecutor = Callable[[str, Dict[str, Any]], Awaitable[str]]


def invalidate_provider_cache():
    """清除提供商缓存 (配置变更后调用)"""
    get_llm_client().invalidate_cache()


# ==================== 伪造检测 ====================

_FABRICATION_PATTERNS = [
    _re.compile(r"(已|已经|我已|我已经|已通过|通过).{0,15}(执行|运行|调用).{0,20}(命令|指令|rm|touch|mkdir|cp|mv|cat|ls|git|docker|pip|npm|cd|echo|python|curl|wget|chmod|chown|kill|bash|sh|find|grep|sed|awk)", _re.IGNORECASE),
    _re.compile(r"(执行了|运行了|已运行|已执行|调用了)\s*.{0,10}(命令|指令|工具|rm|touch|mkdir|cp|mv|cat|git|pip|npm|python)", _re.IGNORECASE),
    _re.compile(r"(已|已经|已成功|成功)(删除|创建|移动|复制|修改|移除|安装|卸载|停止|启动|重启|写入|清除|清空)", _re.IGNORECASE),
    _re.compile(r"(命令|指令).{0,20}(执行|运行).{0,10}(完成|成功|完毕|结果|输出|显示)", _re.IGNORECASE),
    _re.compile(r"(文件|目录|文件夹).{0,30}(不存在|已被删除|已删除|已创建|已移动|已被移除|已清空|已被清空)", _re.IGNORECASE),
    _re.compile(r"/\S+\s+(文件|目录|文件夹)?.{0,5}(不存在|已被?删除|已被?移除|已创建)", _re.IGNORECASE),
    _re.compile(r"(通过|使用|利用).{0,5}(工具|tool).{0,10}(调用|执行|运行)", _re.IGNORECASE),
    _re.compile(r"(执行结果|输出结果|返回结果|运行结果|结果显示|输出显示|结果如下|输出如下)", _re.IGNORECASE),
    _re.compile(r"(No such file|Permission denied|command not found|cannot remove|cannot create|Operation not permitted)", _re.IGNORECASE),
]


def _detect_fabrication(text: str) -> bool:
    """检测模型是否在文本中伪造了工具执行结果"""
    if not text or len(text) < 10:
        return False
    for pattern in _FABRICATION_PATTERNS:
        if pattern.search(text):
            logger.info(f"伪造检测命中模式: {pattern.pattern[:50]}... | 文本片段: {text[:100]}")
            return True
    return False


# ==================== chat_stream (含 tool-calling loop) ====================

async def chat_stream(
    messages: List[Dict[str, Any]],
    model: str = "gpt-4o",
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 8192,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_executor: Optional[ToolExecutor] = None,
    request_id: str = "",
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    流式 AI 对话 (SSE) — 结构化输出, 支持 Tool Calling

    内部委托给 LLMClient.stream() 进行 LLM 通信,
    本层负责 tool-calling loop、伪造检测、结果截断。

    yield 字典类型:
      {"type": "content",    "content": "..."}
      {"type": "thinking",   "content": "..."}
      {"type": "tool_call",  "tool_call": {"id":"..","name":"..","arguments":{..}}}
      {"type": "tool_result","tool_call_id":"..","name":"..","result":"..","duration_ms":N}
      {"type": "tool_error", "tool_call_id":"..","name":"..","error":".."}
      {"type": "usage",      "usage": {...}}
      {"type": "error",      "error": "..."}
    """
    client = get_llm_client()

    # 推理模型禁用 tools
    actual_model_for_check = model.removeprefix(COPILOT_PREFIX)
    is_reasoning = _is_reasoning_model(actual_model_for_check)
    if is_reasoning and tools:
        logger.info(f"推理模型 {actual_model_for_check} 不支持 tools, 跳过工具注入")
        tools = None

    # 工具调用循环状态
    current_messages = list(messages)
    total_tool_rounds = 0
    seen_tool_calls: set = set()
    all_tool_calls_collected: list = []
    fabrication_retries = 0

    while True:
        # 收集流式事件
        pending_tool_calls: Dict[int, Dict[str, Any]] = {}
        started_tool_calls: set = set()
        response_has_content = False
        stream_finish_reason = None
        response_text_parts: list = []
        usage_data = None

        # 决定 tool_choice
        current_tool_choice = "auto"
        if fabrication_retries > 0:
            current_tool_choice = "required"
            logger.info(f"伪造重试中, 强制 tool_choice=required (retry={fabrication_retries})")

        try:
            async for event in client.stream(
                current_messages, model,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=current_tool_choice,
                request_id=request_id,
            ):
                evt_type = event.type
                data = event.data

                if evt_type == "content":
                    yield {"type": "content", "content": data["content"]}
                    response_has_content = True
                    response_text_parts.append(data["content"])

                elif evt_type == "thinking":
                    yield {"type": "thinking", "content": data["content"]}

                elif evt_type == "tool_call_delta":
                    idx = data["tool_call_index"]
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                    tc = pending_tool_calls[idx]
                    if data.get("tool_call_id"):
                        tc["id"] = data["tool_call_id"]
                    if data.get("name"):
                        tc["name"] = data["name"]
                        # ask_user 提前通知
                        if data["name"] == "ask_user" and idx not in started_tool_calls and tc["id"]:
                            started_tool_calls.add(idx)
                            yield {"type": "tool_call_start", "tool_call": {"id": tc["id"], "name": "ask_user"}}
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
                    yield {"type": "error", "error": data.get("error", ""), "error_meta": error_meta}
                    return

        except Exception as e:
            logger.exception("AI chat stream error")
            yield {"type": "error", "error": f"❌ AI 服务异常: {str(e)}"}
            return

        # 发送 usage
        if usage_data:
            yield {"type": "usage", "usage": {
                "prompt_tokens": usage_data.get("prompt_tokens", 0),
                "completion_tokens": usage_data.get("completion_tokens", 0),
                "total_tokens": usage_data.get("total_tokens", 0),
                "tool_rounds": total_tool_rounds,
            }}

        # 检测输出被截断
        if stream_finish_reason == "length":
            if pending_tool_calls:
                logger.info(f"输出因 max_tokens 截断, 丢弃 {len(pending_tool_calls)} 个不完整的工具调用")
                pending_tool_calls.clear()
            if response_has_content:
                yield {"type": "truncated"}

        # 处理 tool calls
        if pending_tool_calls and tool_executor:
            total_tool_rounds += 1
            if total_tool_rounds > max_tool_rounds:
                yield {"type": "content", "content": f"\n\n⚠️ 工具调用已达上限 ({max_tool_rounds}轮)，停止继续调用。"}
                return

            sorted_tcs = sorted(pending_tool_calls.items())
            tool_results_messages: list = []

            # 追加 assistant tool_calls 消息 (OpenAI 协议)
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

                yield {
                    "type": "tool_call",
                    "tool_call": {"id": tc["id"], "name": tc["name"], "arguments": arguments},
                }

                if is_duplicate:
                    result_text = "⚠️ 你已经读取过这个内容了，请直接使用之前的结果，不要重复读取。"
                    yield {
                        "type": "tool_result", "tool_call_id": tc["id"],
                        "name": tc["name"], "arguments": arguments,
                        "result": result_text, "duration_ms": 0,
                    }
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
                    remaining_budget = max_input - current_tokens - max_tokens - 200

                    if result_tokens > remaining_budget and remaining_budget > 500:
                        result_text = truncate_text(result_text, remaining_budget)
                        result_text += f"\n\n[… 内容已截断以适配模型上下文窗口 ({remaining_budget} tokens), 请用 start_line/end_line 指定范围精确读取]"
                    elif remaining_budget <= 500:
                        result_text = truncate_text(result_text, 500)
                        result_text += "\n\n[⚠️ 上下文空间不足, 内容已大幅截断]"

                    yield {
                        "type": "tool_result", "tool_call_id": tc["id"],
                        "name": tc["name"], "arguments": arguments,
                        "result": result_text, "duration_ms": duration_ms,
                    }
                    all_tool_calls_collected.append({
                        "id": tc["id"], "name": tc["name"],
                        "arguments": arguments, "result": result_text,
                        "duration_ms": duration_ms,
                    })
                    tool_results_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text})

                except Exception as e:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    error_msg = f"工具执行失败: {str(e)}"
                    yield {"type": "tool_error", "tool_call_id": tc["id"], "name": tc["name"], "error": error_msg}
                    all_tool_calls_collected.append({
                        "id": tc["id"], "name": tc["name"],
                        "arguments": arguments, "result": f"ERROR: {error_msg}",
                        "duration_ms": duration_ms,
                    })
                    tool_results_messages.append({"role": "tool", "tool_call_id": tc["id"], "content": error_msg})

            current_messages.extend(tool_results_messages)

            # ask_user 中断
            has_ask_user = any(tc["name"] == "ask_user" for _, tc in sorted_tcs)
            if has_ask_user:
                yield {"type": "ask_user_pending"}
                return

            continue  # 回到 while 循环

        else:
            # 无 tool calls — 检测伪造
            if response_has_content and tools and fabrication_retries < 2:
                from studio.backend.api.command_auth import is_fabrication_detection_enabled
                if is_fabrication_detection_enabled():
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
                        yield {"type": "content", "content": "\n\n⚠️ 检测到 AI 伪造执行结果，正在重新要求执行...\n\n"}
                        continue

            if not response_has_content:
                logger.warning(f"模型返回空响应 (finish_reason={stream_finish_reason})")
                yield {"type": "content", "content": "\n\n⚠️ AI 返回了空响应，请重新发送或换个说法试试。"}
            return


async def chat_complete(
    messages: List[Dict[str, Any]],
    model: str = "gpt-4o",
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 8192,
) -> str:
    """同步 AI 对话 (非流式) — 只返回 content 文本"""
    result = []
    async for event in chat_stream(messages, model, system_prompt, temperature, max_tokens):
        if isinstance(event, dict):
            if event.get("type") == "content":
                result.append(event["content"])
            elif event.get("type") == "error":
                result.append(event["error"])
        else:
            result.append(str(event))
    return "".join(result)
