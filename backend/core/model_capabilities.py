"""
设计院 (Studio) - 模型能力数据库 (公共模块)

三层策略获取模型上下文窗口:
  1. API 元数据 / 运行时错误反馈学习 (最优先)
  2. 硬编码已知值
  3. 保守默认值 (兜底)

所有服务共用此模块: models_api, context_manager, ai_service
"""
import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ==================== 硬编码兜底值 ====================
# 仅覆盖主流模型, 未知模型使用保守默认值
# 格式: {model_name: (max_input_tokens, max_output_tokens)}

_STATIC_CONTEXT_WINDOWS: Dict[str, Tuple[int, int]] = {
    # OpenAI
    "gpt-3.5-turbo": (16385, 4096),
    "gpt-4": (8192, 8192),
    "gpt-4-turbo": (128000, 4096),
    "gpt-4o": (128000, 16384),
    "gpt-4o-mini": (128000, 16384),
    "gpt-4.1": (1047576, 32768),
    "gpt-4.1-mini": (1047576, 32768),
    "gpt-5": (1047576, 32768),
    "gpt-5-mini": (1047576, 32768),
    "gpt-5-codex": (1047576, 32768),
    "gpt-5.1": (1047576, 32768),
    "gpt-5.1-codex": (1047576, 32768),
    "gpt-5.1-codex-max": (1047576, 65536),
    "gpt-5.1-codex-mini": (1047576, 32768),
    "gpt-5.2": (1047576, 32768),
    "gpt-5.2-codex": (1047576, 32768),
    "gpt-5.3-codex": (1047576, 32768),
    "o1": (200000, 100000),
    "o1-mini": (128000, 65536),
    "o3": (200000, 100000),
    "o3-mini": (200000, 100000),
    "o4-mini": (200000, 100000),
    # Anthropic
    "claude-3.5-sonnet": (200000, 8192),
    "claude-3.7-sonnet": (200000, 64000),
    "claude-sonnet-4": (200000, 64000),
    "claude-sonnet-4-20250514": (200000, 64000),
    "claude-sonnet-4.5": (1000000, 64000),
    "claude-opus-41": (200000, 64000),
    "claude-opus-4.5": (1000000, 64000),
    "claude-opus-4.6": (1000000, 64000),
    "claude-opus-4.6-fast": (1000000, 64000),
    "claude-haiku-4.5": (200000, 8192),
    # Google
    "gemini-2.0-flash": (1048576, 8192),
    "gemini-2.5-pro": (1048576, 65536),
    "gemini-3-flash-preview": (1048576, 8192),
    "gemini-3-pro-preview": (1048576, 65536),
    # DeepSeek
    "deepseek-r1": (128000, 8192),
    "deepseek-v3": (128000, 8192),
    # xAI
    "grok-3": (131072, 16384),
    "grok-code-fast-1": (131072, 16384),
    # Mistral
    "mistral-large": (128000, 4096),
    "codestral": (256000, 8192),
    # Meta
    "llama-3.3-70b": (128000, 4096),
    "llama-4-scout": (512000, 4096),
    "llama-4-maverick": (1048576, 4096),
    # Microsoft
    "phi-4": (16384, 4096),
}

# 保守默认值: 未知模型
_DEFAULT_INPUT = 128000
_DEFAULT_OUTPUT = 4096


class ModelCapabilityCache:
    """
    模型能力运行时缓存

    四层查询优先级:
      1. DB 手动覆盖 (用户在设置页面设置的)
      2. 运行时学习值 (API元数据 / 错误反馈)
      3. 硬编码兜底表
      4. 保守默认值
    """

    def __init__(self):
        self._learned: Dict[str, Tuple[int, int]] = {}
        self._db_overrides: Dict[str, Tuple[Optional[int], Optional[int]]] = {}

    # ==================== DB 覆盖管理 ====================

    def set_db_override(self, model: str, max_input: Optional[int] = None, max_output: Optional[int] = None):
        """设置 DB 能力覆盖 (最高优先级)"""
        clean = model.removeprefix("copilot:").lower()
        self._db_overrides[clean] = (max_input, max_output)

    def remove_db_override(self, model: str):
        """移除 DB 能力覆盖"""
        clean = model.removeprefix("copilot:").lower()
        self._db_overrides.pop(clean, None)

    def clear_db_overrides(self):
        """清除所有 DB 覆盖"""
        self._db_overrides.clear()

    def get_context_window(self, model: str) -> Tuple[int, int]:
        """
        获取模型的 (max_input_tokens, max_output_tokens)

        model 可以带 copilot: 前缀，会自动去除
        查询优先级: DB 覆盖 > 运行时学习 > 硬编码 > 默认值
        """
        clean = model.removeprefix("copilot:").lower()

        # 层 0: DB 手动覆盖 (最高优先级)
        if clean in self._db_overrides:
            db_in, db_out = self._db_overrides[clean]
            # DB 覆盖可能只覆盖部分字段, 需要从下层补全
            if db_in is not None and db_out is not None:
                return (db_in, db_out)
            # 部分覆盖: 从下层获取默认值再合并
            fallback_in, fallback_out = self._get_without_db(clean)
            return (db_in if db_in is not None else fallback_in,
                    db_out if db_out is not None else fallback_out)

        return self._get_without_db(clean)

    def _get_without_db(self, clean: str) -> Tuple[int, int]:
        """不含 DB 覆盖层的查询 (用于 DB 部分覆盖时的 fallback)"""
        # 层 1: 运行时学习
        if clean in self._learned:
            return self._learned[clean]

        # 层 2: 硬编码 (精确匹配)
        if clean in _STATIC_CONTEXT_WINDOWS:
            return _STATIC_CONTEXT_WINDOWS[clean]

        # 层 2b: 前缀匹配 (gpt-4o-2024-08-06 → gpt-4o)
        for known, val in _STATIC_CONTEXT_WINDOWS.items():
            if clean.startswith(known):
                return val

        # 层 3: 保守默认
        return (_DEFAULT_INPUT, _DEFAULT_OUTPUT)

    def get_max_input(self, model: str) -> int:
        """获取 max_input_tokens"""
        return self.get_context_window(model)[0]

    def get_max_output(self, model: str) -> int:
        """获取 max_output_tokens"""
        return self.get_context_window(model)[1]

    def learn_from_error(self, model: str, error_text: str) -> Optional[int]:
        """
        从错误响应中提取并缓存模型限制

        支持解析:
          - "Max size: 8000 tokens"
          - "maximum context length is 128000 tokens"
          - "Request body too large ... Max size: 8000 tokens"
        """
        clean = model.removeprefix("copilot:").lower()

        patterns = [
            r'Max size:\s*(\d+)\s*tokens',
            r'maximum context length is\s*(\d+)',
            r'max.*?(\d{4,})\s*tokens',
        ]
        for pat in patterns:
            match = re.search(pat, error_text, re.IGNORECASE)
            if match:
                limit = int(match.group(1))
                _, old_output = self.get_context_window(model)
                self._learned[clean] = (limit, old_output)
                logger.info(f"学习到模型 {clean} 的上下文限制: {limit} tokens")
                return limit

        return None

    def learn_from_api(self, model: str, raw: dict) -> bool:
        """
        从 API 元数据中提取 token 限制

        尝试多种字段名 (兼容不同 API 返回格式)
        """
        clean = model.removeprefix("copilot:").lower()

        # 尝试多种可能的字段
        max_input = (
            raw.get("max_input_tokens")
            or raw.get("context_window")
            or raw.get("max_prompt_tokens")
            or _deep_get(raw, "capabilities", "limits", "max_prompt_tokens")
            or _deep_get(raw, "model_limits", "input_tokens")
            or _deep_get(raw, "limits", "max_input_tokens")
            or 0
        )
        max_output = (
            raw.get("max_output_tokens")
            or raw.get("max_completion_tokens")
            or _deep_get(raw, "capabilities", "limits", "max_output_tokens")
            or _deep_get(raw, "model_limits", "output_tokens")
            or _deep_get(raw, "limits", "max_output_tokens")
            or 0
        )

        if max_input > 0 or max_output > 0:
            fallback_in, fallback_out = self.get_context_window(model)
            final_in = max_input if max_input > 0 else fallback_in
            final_out = max_output if max_output > 0 else fallback_out
            self._learned[clean] = (final_in, final_out)
            logger.debug(f"从 API 学习到模型 {clean}: input={final_in}, output={final_out}")
            return True

        return False

    def get_all_known(self) -> Dict[str, Tuple[int, int]]:
        """获取所有已知模型 (合并学习+静态)"""
        merged = dict(_STATIC_CONTEXT_WINDOWS)
        merged.update(self._learned)
        return merged


def _deep_get(d: dict, *keys) -> Optional[int]:
    """安全深层取值"""
    current = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current if isinstance(current, int) else None


# 全局单例
capability_cache = ModelCapabilityCache()
