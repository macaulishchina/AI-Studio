"""
AI 骨干层 (AI Backbone)

提供 AI-Studio 的核心 AI 能力抽象:
  - providers: LLM 提供商协议适配 (GitHub Models / Copilot / OpenAI Compatible)
  - llm: 统一 LLM 客户端 (stream / complete / embed)
  - tools: 统一工具层 (builtin + DB + MCP, 权限, 沙箱, 并行执行)
  - agents: Agent 框架 (ReAct / Planning / Orchestrator)
  - context: 可插拔上下文管道 + 窗口管理
  - rag: 语义检索 (embedding + 向量索引 + 混合检索 + 后台索引)
  - memory: 长期记忆 (事实/决策/偏好, SQLite 持久化)
  - skills: 技能执行引擎 (组合/验证/工具优先级)
  - observability: 可观测性 (trace + metrics + budget)
"""
