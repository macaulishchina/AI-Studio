"""
MCP Tool Adapter — 工具定义格式转换

MCP 工具定义格式 (JSON Schema):
  {
    "name": "create_issue",
    "description": "Create a new issue",
    "inputSchema": {
      "type": "object",
      "properties": {...},
      "required": [...]
    }
  }

Studio 工具定义格式 (OpenAI Function Calling):
  {
    "type": "function",
    "function": {
      "name": "mcp_github__create_issue",
      "description": "...",
      "parameters": {
        "type": "object",
        "properties": {...},
        "required": [...]
      }
    }
  }

命名约定:
  MCP 工具在 Studio 中的名称: mcp_{server_slug}__{tool_name}
  例: "create_issue" on "github" → "mcp_github__create_issue"
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# MCP → Studio 工具名前缀
MCP_TOOL_PREFIX = "mcp_"
MCP_TOOL_SEPARATOR = "__"


def make_studio_tool_name(server_slug: str, mcp_tool_name: str) -> str:
    """生成 Studio 侧的工具名 (MCP 工具在 OpenAI tool calling 中使用)"""
    return f"{MCP_TOOL_PREFIX}{server_slug}{MCP_TOOL_SEPARATOR}{mcp_tool_name}"


def parse_studio_tool_name(studio_name: str) -> Optional[Tuple[str, str]]:
    """解析 Studio 工具名 → (server_slug, mcp_tool_name)

    Returns:
        (server_slug, mcp_tool_name) 或 None (非 MCP 工具)
    """
    if not studio_name.startswith(MCP_TOOL_PREFIX):
        return None

    rest = studio_name[len(MCP_TOOL_PREFIX):]
    if MCP_TOOL_SEPARATOR not in rest:
        return None

    parts = rest.split(MCP_TOOL_SEPARATOR, 1)
    if len(parts) != 2:
        return None

    return parts[0], parts[1]


def is_mcp_tool(studio_name: str) -> bool:
    """判断是否为 MCP 工具"""
    return parse_studio_tool_name(studio_name) is not None


def mcp_tool_to_openai(
    mcp_tool: Dict[str, Any],
    server_slug: str,
    description_prefix: str = "",
) -> Dict[str, Any]:
    """将 MCP 工具定义转换为 OpenAI Function Calling 格式

    Args:
        mcp_tool: MCP 工具定义 (name, description, inputSchema)
        server_slug: MCP server 标识
        description_prefix: 描述前缀 (如 "[GitHub] ")

    Returns:
        OpenAI tools format dict
    """
    mcp_name = mcp_tool.get("name", "unknown")
    studio_name = make_studio_tool_name(server_slug, mcp_name)

    description = mcp_tool.get("description", "")
    if description_prefix:
        description = f"{description_prefix}{description}"

    # MCP inputSchema → OpenAI parameters
    input_schema = mcp_tool.get("inputSchema", {})
    parameters = {
        "type": input_schema.get("type", "object"),
        "properties": input_schema.get("properties", {}),
    }
    if "required" in input_schema:
        parameters["required"] = input_schema["required"]

    return {
        "type": "function",
        "function": {
            "name": studio_name,
            "description": description,
            "parameters": parameters,
        },
    }


def mcp_tools_to_openai(
    mcp_tools: List[Dict[str, Any]],
    server_slug: str,
    server_name: str = "",
) -> List[Dict[str, Any]]:
    """批量转换 MCP 工具列表为 OpenAI 格式"""
    prefix = f"[{server_name}] " if server_name else ""
    return [
        mcp_tool_to_openai(tool, server_slug, prefix)
        for tool in mcp_tools
    ]


def openai_args_to_mcp(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """将 OpenAI tool call 的 arguments 转换为 MCP 格式

    目前两者格式一致 (都是 JSON object), 此函数作为扩展点预留
    """
    return arguments


def mcp_result_to_text(mcp_result: Dict[str, Any]) -> str:
    """将 MCP 工具调用结果转换为纯文本 (供 AI 消费)

    MCP 结果格式:
      {"content": [{"type": "text", "text": "..."}, {"type": "image", ...}], "isError": bool}
    """
    if "error" in mcp_result:
        return f"⚠️ MCP 工具错误: {mcp_result['error']}"

    contents = mcp_result.get("content", [])
    if not contents:
        return "(无输出)"

    parts = []
    for item in contents:
        item_type = item.get("type", "text")
        if item_type == "text":
            parts.append(item.get("text", ""))
        elif item_type == "image":
            parts.append("[图片数据 - 已省略]")
        elif item_type == "resource":
            uri = item.get("resource", {}).get("uri", "?")
            text = item.get("resource", {}).get("text", "")
            parts.append(f"[资源: {uri}]\n{text}")
        else:
            parts.append(f"[{item_type}: {str(item)[:200]}]")

    result_text = "\n".join(parts)

    if mcp_result.get("isError"):
        result_text = f"⚠️ MCP 工具执行失败:\n{result_text}"

    return result_text
