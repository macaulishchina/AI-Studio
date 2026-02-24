"""
设计院 (Studio) - Token 估算工具 (公共模块)

使用 tiktoken (OpenAI tokenizer) 进行本地估算。
非 OpenAI 模型用 cl100k_base 近似 (误差 < 15%)。
如果 tiktoken 未安装则使用字符数粗估。
"""
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# 延迟初始化 tiktoken (可能未安装)
_encoder = None
_tiktoken_available = None


def _get_encoder():
    """延迟初始化 tiktoken 编码器"""
    global _encoder, _tiktoken_available
    if _tiktoken_available is None:
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("cl100k_base")
            _tiktoken_available = True
            logger.info("tiktoken 初始化成功 (cl100k_base)")
        except ImportError:
            _tiktoken_available = False
            logger.warning("tiktoken 未安装, 使用字符数粗估 (建议 pip install tiktoken)")
        except Exception as e:
            _tiktoken_available = False
            logger.warning(f"tiktoken 初始化失败: {e}, 使用字符数粗估")
    return _encoder


def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数量

    有 tiktoken: 精确计算 (cl100k_base)
    无 tiktoken: 粗估 (中文 ≈ 2 char/token, 英文 ≈ 4 char/token, 取平均 3)
    """
    if not text:
        return 0

    encoder = _get_encoder()
    if encoder is not None:
        return len(encoder.encode(text))

    # 粗估: 混合中英文取平均 ~3 chars/token
    return max(1, len(text) // 3)


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """
    估算消息列表的 token 数量

    包含 OpenAI 消息格式的 overhead:
      每条消息 +4 tokens (role + content 分隔)
      末尾 +3 tokens (assistant reply priming)
    """
    total = 0
    for msg in messages:
        total += 4  # message overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # multipart content (text + image_url)
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += estimate_tokens(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        total += 765  # 图片固定估算 ~765 tokens (low detail)
        role = msg.get("role", "")
        total += estimate_tokens(role)
    total += 3  # reply priming
    return total


def truncate_text(text: str, max_tokens: int) -> str:
    """
    将文本截断到指定 token 数以内

    Returns: 截断后的文本 (如果截断则末尾追加 '...(截断)')
    """
    if not text or max_tokens <= 0:
        return ""

    current = estimate_tokens(text)
    if current <= max_tokens:
        return text

    encoder = _get_encoder()
    if encoder is not None:
        tokens = encoder.encode(text)
        truncated_tokens = tokens[:max_tokens - 5]  # 留几个 token 给截断标记
        truncated = encoder.decode(truncated_tokens)
        return truncated + "\n...(截断)"

    # 粗估截断
    ratio = max_tokens / current
    cut_pos = int(len(text) * ratio) - 20
    return text[:max(0, cut_pos)] + "\n...(截断)"
