"""
设计院 (Studio) - AI 服务提供商预设数据

内置提供商 (不可删除):
  - github: GitHub Models API (免费, 用 GITHUB_TOKEN)
  - copilot: GitHub Copilot API (需订阅, 用 OAuth Device Flow)

预设第三方提供商 (默认禁用, 填入 API Key 后启用):
  - deepseek: DeepSeek
  - qwen: 通义千问 (阿里)
  - zhipu: 智谱 GLM
  - kimi: 月之暗面 Kimi

所有第三方提供商均走 OpenAI 兼容 API 格式。
"""
from typing import List, Dict, Any


# ==================== 内置提供商 ====================

BUILTIN_PROVIDERS: List[Dict[str, Any]] = [
    {
        "slug": "github",
        "name": "GitHub Models",
        "provider_type": "github_models",
        "base_url": "https://models.inference.ai.azure.com",
        "api_key": "",  # 使用环境变量 GITHUB_TOKEN
        "enabled": True,
        "is_builtin": True,
        "is_preset": False,
        "icon": "🐙",
        "description": "GitHub Models API — 免费调用，使用 GITHUB_TOKEN 认证。支持 OpenAI, Meta, DeepSeek, Mistral 等模型。",
        "default_models": [],
    },
    {
        "slug": "copilot",
        "name": "GitHub Copilot",
        "provider_type": "copilot",
        "base_url": "https://api.githubcopilot.com",
        "api_key": "",  # 使用 OAuth Device Flow
        "enabled": True,
        "is_builtin": True,
        "is_preset": False,
        "icon": "☁️",
        "description": "GitHub Copilot API — 需要 Copilot Pro/Pro+ 订阅，通过 OAuth 设备流授权。支持 Claude, Gemini, Grok 等高级模型。",
        "default_models": [],
    },
]


# ==================== 预设第三方提供商 ====================

PRESET_PROVIDERS: List[Dict[str, Any]] = [
    {
        "slug": "deepseek",
        "name": "DeepSeek",
        "provider_type": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "enabled": False,
        "is_builtin": False,
        "is_preset": True,
        "icon": "🔮",
        "description": "DeepSeek — 高性价比推理模型。注册 https://platform.deepseek.com 获取 API Key。",
        "default_models": [
            {"name": "deepseek-chat", "friendly_name": "DeepSeek Chat (V3)", "model_family": "deepseek",
             "tags": ["agents", "multipurpose"], "summary": "通用对话模型, 性价比极高"},
            {"name": "deepseek-reasoner", "friendly_name": "DeepSeek Reasoner (R1)", "model_family": "deepseek",
             "tags": ["reasoning"], "summary": "推理模型, 支持思维链"},
        ],
    },
    {
        "slug": "qwen",
        "name": "通义千问",
        "provider_type": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "enabled": False,
        "is_builtin": False,
        "is_preset": True,
        "icon": "🌐",
        "description": "阿里云通义千问 — 国内领先大模型。注册 https://dashscope.console.aliyun.com 获取 API Key。",
        "default_models": [
            {"name": "qwen-turbo-latest", "friendly_name": "Qwen Turbo", "model_family": "qwen",
             "tags": ["agents", "multipurpose"], "summary": "快速响应, 成本最低"},
            {"name": "qwen-plus-latest", "friendly_name": "Qwen Plus", "model_family": "qwen",
             "tags": ["agents", "multipurpose"], "summary": "均衡性能, 推荐日常使用"},
            {"name": "qwen-max-latest", "friendly_name": "Qwen Max", "model_family": "qwen",
             "tags": ["agents", "multimodal"], "summary": "旗舰模型, 复杂任务首选"},
        ],
    },
    {
        "slug": "zhipu",
        "name": "智谱 GLM",
        "provider_type": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": "",
        "enabled": False,
        "is_builtin": False,
        "is_preset": True,
        "icon": "🧠",
        "description": "智谱 AI (GLM) — 清华系大模型。注册 https://open.bigmodel.cn 获取 API Key。",
        "default_models": [
            {"name": "glm-4-plus", "friendly_name": "GLM-4 Plus", "model_family": "zhipu",
             "tags": ["agents", "multipurpose"], "summary": "旗舰模型, 综合能力最强"},
            {"name": "glm-4-flash", "friendly_name": "GLM-4 Flash", "model_family": "zhipu",
             "tags": ["agents", "multipurpose"], "summary": "极速推理, 免费额度充足"},
            {"name": "glm-4-long", "friendly_name": "GLM-4 Long", "model_family": "zhipu",
             "tags": ["agents"], "summary": "长文本处理, 支持 1M context"},
        ],
    },
    {
        "slug": "kimi",
        "name": "月之暗面 Kimi",
        "provider_type": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key": "",
        "enabled": False,
        "is_builtin": False,
        "is_preset": True,
        "icon": "🌙",
        "description": "月之暗面 Kimi — 长上下文对话模型。注册 https://platform.moonshot.cn 获取 API Key。",
        "default_models": [
            {"name": "moonshot-v1-8k", "friendly_name": "Kimi 8K", "model_family": "kimi",
             "tags": ["multipurpose"], "summary": "8K 上下文, 快速响应"},
            {"name": "moonshot-v1-32k", "friendly_name": "Kimi 32K", "model_family": "kimi",
             "tags": ["multipurpose"], "summary": "32K 上下文, 日常推荐"},
            {"name": "moonshot-v1-128k", "friendly_name": "Kimi 128K", "model_family": "kimi",
             "tags": ["multipurpose"], "summary": "128K 超长上下文"},
        ],
    },
]


# 全部预设 (内置 + 第三方)
ALL_SEED_PROVIDERS = BUILTIN_PROVIDERS + PRESET_PROVIDERS
