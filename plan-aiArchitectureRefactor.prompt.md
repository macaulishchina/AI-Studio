# AI 架构全面重构 — 从工具循环到 Agent 平台

**TL;DR**: 当前 AI 架构本质是一个**扁平的 tool-calling 循环**嵌入在 `ai_service.chat_stream()` 中，没有正式的 Agent 抽象、RAG 检索、Skill 运行时、长期记忆或可观测性。重构将引入全新的 `backend/ai/` 包作为 AI 骨干层，包含 7 个子系统：**Provider（提供商抽象）→ LLM（统一客户端）→ Tools（统一工具层）→ Agents（Agent 框架）→ Context + RAG + Memory（上下文三件套）→ Skills（技能引擎）→ Observability（可观测性）**。现有 `services/ai_service.py`（888行）、`task_runner.py`（1077行）、`tool_registry.py`（1083行）、`context_service.py`（505行）将被拆解重组，API 端点和数据库表相应扩展。采用 **6 阶段渐进式** 实施，每阶段可独立验证。

---

## 目标架构全景

```
backend/ai/                        ← 新建 AI 骨干包
├── providers/                     ← Phase 1: 提供商抽象
│   ├── base.py                    # BaseProvider ABC
│   ├── github_models.py           # GitHub Models API
│   ├── copilot.py                 # Copilot API (含 billing 模拟)
│   └── openai_compat.py           # 通用 OpenAI 兼容
│
├── llm.py                         ← Phase 1: 统一 LLM 客户端
│                                  # stream() / complete() / embed()
│                                  # 事件协议、provider 路由、重试策略
│
├── tools/                         ← Phase 2: 统一工具层
│   ├── registry.py                # ToolRegistry 单例 (定义 + 权限 + 发现)
│   ├── executor.py                # ToolExecutor (沙箱 + 调度 + 并行)
│   ├── builtin/                   # 内置工具拆分
│   │   ├── file_ops.py            # read_file, search_text, list_dir, file_tree
│   │   ├── commands.py            # run_command + 审批流
│   │   └── interaction.py         # ask_user
│   └── mcp/                       # MCP 子系统 (从 services/mcp/ 迁入)
│       ├── client.py              # MCPClientManager 精简
│       ├── adapter.py             # 格式转换 + 工具发现
│       ├── config.py              # MCPServerRegistry
│       ├── secrets.py             # 凭据解析
│       └── audit.py               # 审计 + 限流
│
├── agents/                        ← Phase 3: Agent 框架
│   ├── base.py                    # BaseAgent ABC (plan→act→reflect→finalize)
│   ├── react.py                   # ReActAgent (增强版单 Agent, 含反思)
│   ├── orchestrator.py            # AgentOrchestrator (多 Agent 编排)
│   ├── strategies.py              # 执行策略 (single/planning/review/multi)
│   └── protocols.py               # Agent 间通信协议
│
├── context/                       ← Phase 4: 上下文 + RAG + Memory
│   ├── builder.py                 # ContextBuilder (可插拔 source 管道)
│   ├── sources/                   # 上下文源插件
│   │   ├── base.py                # BaseContextSource ABC
│   │   ├── workspace.py           # 工作区文件发现 (from context_service)
│   │   ├── rag.py                 # RAG 检索源
│   │   ├── memory.py              # 长期记忆源
│   │   └── role.py                # 角色/技能 prompt 源
│   ├── window.py                  # 上下文窗口管理 (from context_manager)
│   └── compression.py             # 摘要 + 截断策略
│
├── rag/                           ← Phase 4: RAG 子系统
│   ├── embeddings.py              # Embedding 服务 (复用 LLM provider 或本地)
│   ├── index.py                   # VectorIndex (numpy cosine + SQLite 持久化)
│   ├── chunker.py                 # 文档分块策略 (代码感知)
│   ├── retriever.py               # 语义检索器
│   └── indexer.py                 # 后台索引任务 (文件变更 → 增量索引)
│
├── memory/                        ← Phase 4: 长期记忆
│   ├── store.py                   # MemoryStore ABC + SQLite 实现
│   ├── facts.py                   # 项目事实记忆 (自动提取关键事实)
│   ├── decisions.py               # 决策记录 (上下文 + 理由 + 结果)
│   └── preferences.py             # 用户偏好学习
│
├── skills/                        ← Phase 5: 技能引擎
│   ├── engine.py                  # SkillEngine (运行时行为调度)
│   ├── behaviors.py               # 行为类型 (prompt_only/tool_routing/output_constrained/pipeline)
│   ├── validator.py               # 输出约束验证
│   └── catalog.py                 # 技能发现与加载
│
└── observability/                 ← Phase 6: 可观测性
    ├── tracer.py                  # 调用链追踪 (LLM→Tool→Agent→Result)
    ├── metrics.py                 # Token/延迟/成本指标收集
    ├── budget.py                  # 项目/用户 Token 预算管理
    └── dashboard.py               # 聚合查询 (供 API 调用)
```

---

## Phase 1: Provider 抽象 + LLM 客户端 (基础层)

**目标**: 将 `backend/services/ai_service.py` 的 888 行拆解为**干净的分层结构**：Provider 负责协议差异，LLM 负责统一接口。

### Steps

1. 创建 `backend/ai/__init__.py` 和 `backend/ai/providers/__init__.py` 包结构

2. 抽取 `BaseProvider` ABC 到 `backend/ai/providers/base.py`:
   - 定义接口: `async stream(messages, model, **kwargs) → AsyncGenerator[ProviderEvent]`
   - 定义接口: `async complete(messages, model, **kwargs) → CompletionResult`
   - 定义接口: `async embed(texts, model) → List[List[float]]` (为 RAG 预留)
   - 定义 `ProviderEvent` 协议 (content_delta / thinking_delta / tool_call_delta / usage / error)
   - 定义 `ProviderCapability` (streaming / tools / vision / reasoning / embeddings)

3. 从 `ai_service.py` 抽取三个 Provider 实现:
   - `github_models.py`: 提取 L56-L150 的 GitHub Models 请求构建 + L548-L610 的 SSE 解析
   - `copilot.py`: 提取 L44-L65 的 Copilot billing 头 + L475-L540 的 Copilot 特殊处理
   - `openai_compat.py`: 提取 L165-L200 的第三方 API 构建逻辑
   - 每个 Provider 封装自己的 `httpx.AsyncClient` 生命周期、header 构建、错误解析

4. 创建 `backend/ai/llm.py` 统一 LLM 客户端:
   - `LLMClient` 类: 持有 Provider 池 + `_resolve_provider()` 路由逻辑（从 `ai_service.py` L220 提取）
   - `stream()` 方法: 消费 Provider 的 `ProviderEvent` → 发射统一的 `LLMEvent`
   - `complete()` 方法: 非流式调用
   - `embed()` 方法: embedding 调用（复用 Provider 或调用本地模型）
   - **不含 tool loop** — 纯 LLM 通信层
   - Provider 缓存从全局 dict 改为 `LLMClient` 实例属性

5. 兼容性桥接: 在 `backend/services/ai_service.py` 保留 `chat_stream()` 和 `chat_complete()` 函数签名作为 **thin wrapper**，内部委托给 `LLMClient`。这样 `task_runner.py`、`discussion.py`、`context_manager.py` 的导入暂时不用改

6. 新增 `requirements.txt` 依赖: `numpy>=1.24.0` (为 Phase 4 RAG 向量运算预备)

---

## Phase 2: 统一工具层 (Tools)

**目标**: 将 `tool_registry.py` 的 1083 行拆解，统一 builtin / DB / MCP 三种工具源，支持**并行工具执行**。

### Steps

1. 创建 `backend/ai/tools/` 包，定义核心接口:
   - `ToolDefinition` dataclass: name, description, parameters (JSON Schema), permission_keys, source (builtin/db/mcp), executor_ref
   - `ToolResult` dataclass: success, output, duration_ms, metadata
   - `ToolExecutor` protocol: `async execute(name, arguments, context: ToolContext) → ToolResult`
   - `ToolContext` dataclass: workspace_path, permissions, project_id, approval_callback, workspace_dir

2. 拆分内置工具到 `backend/ai/tools/builtin/`:
   - `file_ops.py`: 提取 `tool_registry.py` 中的 `_tool_read_file()` (L560)、`_tool_search_text()` (L660)、`_tool_list_directory()` (L760)、`_tool_get_file_tree()` (L830) — 每个函数改为类方法，实现 `ToolExecutor` 接口
   - `commands.py`: 提取 `_tool_run_command_unrestricted()` (L920) + `_is_readonly_command()` (L870) + 命令审批逻辑 (L494-L557)
   - `interaction.py`: 提取 `_tool_ask_user()` (L550) — ask_user 特殊处理

3. 创建 `ToolRegistry` 类 (`backend/ai/tools/registry.py`):
   - `register(definition: ToolDefinition)` — 注册直接定义
   - `register_builtin()` — 自动发现 `builtin/` 目录下的工具
   - `load_from_db()` — 加载 DB `ToolDefinition` 表
   - `discover_mcp()` — 合并 MCP 工具
   - `get_definitions(permissions) → List[dict]` — 过滤后输出 OpenAI format
   - `get_executor(name) → ToolExecutor` — 路由到正确的执行器
   - 替代现有的 `_db_tool_cache` / `_db_perm_map_cache` 全局变量

4. 创建 `ToolExecutorEngine` (`backend/ai/tools/executor.py`):
   - `execute_single(name, args, ctx) → ToolResult`
   - `execute_parallel(calls: List[ToolCall], ctx) → List[ToolResult]` — **并行执行工具**，复用 `asyncio.gather()` + per-tool 超时
   - 权限检查、路径沙箱、结果截断、审计日志 — 统一在此层

5. 迁移 MCP 子系统: 将 `backend/services/mcp/` 移至 `backend/ai/tools/mcp/`，接口对齐:
   - `MCPExecutionAdapter` 实现 `ToolExecutor` 接口
   - `MCPToolAdapter` 的 `to_openai_format()` 改为返回 `ToolDefinition`
   - 保留 `MCPClientManager`、`MCPServerRegistry` 核心逻辑不变

6. 兼容性: `backend/services/tool_registry.py` 瘦身为 re-export 层:
   ```python
   from studio.backend.ai.tools.registry import ToolRegistry
   # 保持原有函数签名，委托给 ToolRegistry 单例
   ```

---

## Phase 3: Agent 框架

**目标**: 将 `ai_service.chat_stream()` 中嵌入的 tool loop (L468-L830) 和 `task_runner._execute_discussion()` 中的执行逻辑 (L618-L951) 提取为正式的 **Agent 框架**，支持 ReAct、Planning、Multi-Agent 三种模式。

### Steps

1. 定义 `BaseAgent` ABC (`backend/ai/agents/base.py`):
   ```python
   class BaseAgent:
       llm: LLMClient
       tools: ToolRegistry
       context_builder: ContextBuilder
       memory: MemoryStore  # optional
       tracer: Tracer       # optional
       
       async run(input: AgentInput) → AsyncGenerator[AgentEvent]:
           # Template method: plan → act → reflect → finalize
       
       async plan(input, context) → AgentPlan       # 可选: 任务分解
       async act(plan_step) → AgentStepResult        # 执行单步 (LLM + tools)
       async reflect(steps_so_far) → ReflectionResult # 自我评估
       async finalize(all_steps) → AgentOutput        # 汇总输出
   ```
   - `AgentInput`: messages, system_prompt, tools, constraints, max_rounds
   - `AgentEvent`: 与现有 SSE 协议兼容 (content/thinking/tool_call/tool_result/plan_step/reflection/done/error)
   - `AgentConfig`: model, temperature, max_tool_rounds, enable_planning, enable_reflection, parallel_tools

2. 实现 `ReActAgent` (`backend/ai/agents/react.py`):
   - **核心**: 将 `ai_service.chat_stream()` L468-L830 的 tool loop 提取为 `act()` 循环
   - **增强 1 — Reflection**: 每 N 轮 (可配，默认 5) 在循环中插入一轮 reflection，检查是否偏离目标、是否在重复操作
   - **增强 2 — Fabrication Guard**: 提取 `_detect_fabrication()` 为独立的 reflection step
   - **增强 3 — Parallel Tools**: 当 LLM 返回多个 tool_calls 时，调用 `ToolExecutorEngine.execute_parallel()`
   - **增强 4 — Error Recovery**: tool 执行失败时不直接报错，而是让 LLM 看到错误后自行决定重试或替代方案
   - 保持与现有 `chat_stream()` 相同的事件协议，确保前端 SSE 无需改动

3. 实现 `PlanningAgent` (扩展 `ReActAgent`):
   - `plan()` 阶段: 使用一次 LLM 调用生成 step-by-step 计划 (JSON schema 约束输出)
   - `act()` 阶段: 按计划逐步执行，每步完成后更新计划状态
   - `reflect()` 阶段: 评估当前进度，必要时修改计划
   - 计划作为 `AgentEvent(type="plan_update")` 发射给前端

4. 实现 `AgentOrchestrator` (`backend/ai/agents/orchestrator.py`):
   - Supervisor 模式: 一个 "调度 Agent" 决定何时切换到哪个 Worker Agent
   - Worker Agent 池: 从 `Role` 配置中创建，每个 Role 对应一个预配置的 Agent
   - 通信协议: Worker 产出 → Supervisor 评审 → 决定下一步 (继续 / 切换 / 完成)
   - **与 Workflow 集成**: 在 `project_types.py` 中为每个 stage 配置 `agent_strategy`: "react" / "planning" / "orchestrated"
   - 默认行为: 讨论阶段用 `ReActAgent`，实施阶段用 `PlanningAgent`

5. 重构 `task_runner._execute_discussion()`:
   - 现有 L618-L951 的讨论执行逻辑 → 简化为:
     1. 构建 `AgentInput`
     2. 根据项目 `agent_strategy` 选择 Agent 类型
     3. `async for event in agent.run(input):` 分发到 EventBus
     4. 持久化最终消息
   - `TaskManager` 保持不变 (任务生命周期管理)
   - `ProjectEventBus` 提取到 `backend/ai/events.py` (通用事件总线)，`task_runner.py` 从那里导入

6. 扩展 SSE 事件协议 (新增类型给前端):
   - `plan_update`: Agent 计划创建/更新 `{plan: [{step, status, result}]}`
   - `reflection`: Agent 自我反思 `{reflection: string, action: "continue"|"adjust"|"abort"}`
   - `agent_switch`: 多 Agent 切换 `{from_agent, to_agent, reason}`
   - 前端 `useProjectEventBus.ts` 需相应更新

---

## Phase 4: Context + RAG + Memory (上下文三件套)

**目标**: 将 `context_service.py` 和 `context_manager.py` 重构为**可插拔的上下文管道**，新增 RAG 语义检索和长期记忆。

### Steps

1. 创建 `ContextBuilder` (`backend/ai/context/builder.py`):
   - 可插拔 `ContextSource` 管道:
     ```python
     sources: List[ContextSource]  # 按优先级排序
     
     def build(budget_tokens, project, role, skills, ...) -> SystemPrompt:
         sections = []
         for source in sources:
             sections.extend(source.gather(budget_remaining, project))
         return assemble(sections, budget_tokens)
     ```
   - 每个 `ContextSource` 实现 `gather() → List[NamedSection]`，带优先级和可裁剪标记
   - 预算分配改为**竞争式**: 高优先级源先占预算，低优先级源用剩余空间

2. 实现 5 个 ContextSource:
   - `RoleContextSource`: 从 `context_service.py` L284-L350 提取角色/策略 prompt
   - `SkillContextSource`: 从 `context_service.py` L370-L432 提取技能注入
   - `WorkspaceContextSource`: 从 `context_service.py` L250-L280 提取文件发现 + 项目树
   - `RAGContextSource`: **新增** — 从 RAG 索引检索与当前消息语义相关的代码片段
   - `MemoryContextSource`: **新增** — 注入项目事实记忆和关键决策

3. 迁移 `context_manager.py` → `backend/ai/context/window.py`:
   - `prepare_context()`, `_truncate_messages()` 保持逻辑不变
   - `summarize_context_if_needed()` 改为调用 `LLMClient.complete()` (不再直接依赖 ai_service)

4. **RAG 子系统** (`backend/ai/rag/`):
   - `embeddings.py`:
     - `EmbeddingService` — 优先使用 AI Provider 的 embedding endpoint (text-embedding-3-small 等)
     - Fallback: 使用本地简易 TF-IDF 向量化（零外部依赖）
     - 缓存: embedding 结果存入 SQLite `embedding_cache` 表
   - `chunker.py`:
     - `CodeChunker` — 代码感知分块 (按函数/类/模块分割)
     - `TextChunker` — 通用文本分块 (按段落/句子, 含 overlap)
     - chunk size 适配模型 embedding 窗口 (默认 512 tokens)
   - `index.py`:
     - `VectorIndex` 类 — numpy cosine similarity 内存索引
     - SQLite 持久化: `vector_embeddings` 表 (file_path, chunk_id, content, embedding BLOB, updated_at)
     - 支持增量更新: 文件变更时只重新索引变更的 chunk
     - 索引加载: 启动时从 SQLite 读入内存
   - `retriever.py`:
     - `RAGRetriever.retrieve(query, top_k=5) → List[RetrievedChunk]`
     - 支持混合检索: 向量相似度 + keyword matching (BM25-like 加权)
   - `indexer.py`:
     - `BackgroundIndexer` — workspace 文件索引管理
     - 启动时全量索引 (可配置 watched_patterns: `**/*.py`, `**/*.ts` 等)
     - 文件变更监听 (可选, 或定时 re-index)
     - 跳过 `.gitignore` 中的文件和二进制文件

5. **Memory 子系统** (`backend/ai/memory/`):
   - `store.py`:
     - `MemoryStore` ABC: `save_fact()`, `query_facts()`, `save_decision()`, `query_decisions()`
     - `SQLiteMemoryStore` 实现
   - `facts.py`:
     - `FactExtractor` — 对话结束后，用 LLM 从对话中提取关键事实
     - 事实格式: `{subject, predicate, object, source_message_id, confidence}`
     - 例: `{项目, 使用, FastAPI}`, `{部署环境, 是, Docker + K8s}`
   - `decisions.py`:
     - `DecisionRecorder` — 当检测到方案确定时记录
     - 决策格式: `{title, context, alternatives, chosen, reason, message_id}`
   - `preferences.py`:
     - `PreferenceTracker` — 隐式学习用户偏好 (代码风格, 语言偏好, 详细度偏好)
     - 触发: 用户修正 AI 输出时自动记录

6. 新增数据库表 (在 `models.py` 中添加):
   - `VectorEmbedding`: file_path, chunk_id, chunk_content, embedding (LargeBinary), chunk_metadata (JSON), project_id, updated_at
   - `MemoryFact`: project_id, subject, predicate, object, source_message_id, confidence, created_at
   - `MemoryDecision`: project_id, title, context, alternatives (JSON), chosen, reason, message_id, created_at
   - `UserPreference`: user_id, key, value, source, updated_at

---

## Phase 5: Skill 执行引擎

**目标**: 将 Skill 从纯 prompt 模板升级为**可执行模块**，支持运行时行为调度、工具路由和输出约束验证。

### Steps

1. 扩展 `Skill` 数据模型 (在 `models.py` 修改):
   - 新增字段:
     - `behavior_type`: enum ("prompt_only" / "tool_routing" / "output_constrained" / "pipeline")
     - `required_tools`: JSON list — 自动激活的工具名 (从 recommended 升级为 required)
     - `output_schema`: JSON Schema — 输出结构约束
     - `validation_rules`: JSON list — 运行时验证规则
     - `pre_hooks`: JSON list — 执行前的准备步骤 (如 RAG 检索特定范围)
     - `post_hooks`: JSON list — 执行后的处理步骤 (如事实提取、决策记录)

2. 创建 `SkillEngine` (`backend/ai/skills/engine.py`):
   - `activate(skill, agent)` — 将技能注入 Agent 运行时:
     - `prompt_only`: 仅注入 system prompt (现有行为)
     - `tool_routing`: 自动启用 `required_tools`，禁用无关工具，修改 tool_choice
     - `output_constrained`: 注入 output schema 到 system prompt + 输出后验证
     - `pipeline`: 编排多步执行 (如"先搜索 → 再分析 → 再总结")
   - `validate_output(skill, output) → ValidationResult` — 检查输出是否满足 skill 约束

3. 创建 `SkillBehaviors` (`backend/ai/skills/behaviors.py`):
   - 每种 `behavior_type` 的具体实现:
     - `PromptOnlyBehavior`: 注入 prompt，不修改运行时 (backward compatible)
     - `ToolRoutingBehavior`: 修改 Agent 可用工具集 + tool_choice 策略
     - `OutputConstrainedBehavior`: 注入 JSON Schema，执行后调用 `validator.validate()`
     - `PipelineBehavior`: 将 skill 分解为多个 sub-step，每步可有不同的 prompt + tools

4. 创建 `SkillValidator` (`backend/ai/skills/validator.py`):
   - JSON Schema 验证 (用 pydantic 或 jsonschema)
   - 自定义规则验证 (如 "输出必须包含 API 端点列表", "代码块必须有语言标注")
   - 验证失败时: Agent 自动重试一轮，给 LLM 看验证错误让它修正

5. 更新 `ContextBuilder` 的 `SkillContextSource`: 根据 `behavior_type` 差异化注入 prompt

6. 更新前端 `Settings > 技能管理`: 新增 behavior_type 选择、required_tools 多选、output_schema 编辑器、validation_rules 配置

---

## Phase 6: 可观测性 & 成本控制

**目标**: 引入 LLM 调用链追踪、Token 成本统计、项目级预算管理。

### Steps

1. 创建 `Tracer` (`backend/ai/observability/tracer.py`):
   - `TraceContext` — 贯穿 Agent 全生命周期的追踪上下文
   - 自动记录: LLM 调用 (model, tokens, latency)、工具调用 (name, duration, result_size)、Agent 步骤 (plan/act/reflect)
   - 持久化到 `ai_traces` 表 (JSON 格式的 span 树)
   - 与 `AiTask` 关联: 每个 task 有一个 trace_id

2. 创建 `MetricsCollector` (`backend/ai/observability/metrics.py`):
   - 聚合指标: 按 project/user/model 维度统计 token 用量、调用次数、平均延迟
   - 持久化到 `cost_tracking` 表 (hourly/daily 聚合)
   - 基于 `models_api.py` 中已有的 pricing 数据计算成本

3. 创建 `BudgetManager` (`backend/ai/observability/budget.py`):
   - 项目级 Token 预算 (daily/monthly)
   - 预算超额策略: warn / throttle / block
   - 检查点: 在 Agent 每轮 LLM 调用前检查预算

4. 新增数据库表:
   - `AiTrace`: trace_id, task_id, project_id, spans (JSON), total_tokens, total_cost, created_at
   - `CostTracking`: project_id, user_id, model, period (hourly/daily), input_tokens, output_tokens, cost, recorded_at
   - `ProjectBudget`: project_id, daily_token_limit, monthly_token_limit, alert_threshold_pct

5. 新增 API 端点:
   - `GET /studio-api/observability/traces/{task_id}` — 查看调用链
   - `GET /studio-api/observability/costs` — 成本汇总 (可按 project/user/model/period 过滤)
   - `GET /studio-api/observability/budget/{project_id}` — 预算使用情况
   - `PUT /studio-api/observability/budget/{project_id}` — 设置预算

6. 前端新增页面:
   - `Settings > AI 服务` 下新增 "成本监控" 子页面
   - 项目详情页新增 "Trace 查看器" (可视化 Agent 执行步骤)

---

## Phase 7: 前端集成

### Steps

1. 更新 `useProjectEventBus.ts`:
   - 处理新事件: `plan_update`, `reflection`, `agent_switch`
   - Agent 计划状态管理 (展示步骤进度条)

2. 更新 `ChatPanel.vue`:
   - 新增 Agent 计划可视化 (折叠面板显示当前计划+步骤状态)
   - Reflection 消息展示 (区别于普通 assistant 消息的样式)
   - 并行工具调用展示 (多个工具卡片并排)
   - 记忆命中标记 (显示 "基于项目记忆" 标签)

3. 新增 Store: `stores/aiConfig.ts` — 管理 Agent 策略、预算、RAG 配置

4. 更新 Settings:
   - `AI 服务 > 推理偏好`: 新增 Agent 策略选择 (react / planning / orchestrated)
   - `工作流 > 工具管理`: 合并显示 builtin + DB + MCP 工具统一视图
   - `系统管理`: 新增成本监控、预算管理
   - `项目详情页`: Memory 开关 + RAG 索引状态

5. 更新 `frontend/src/api/index.ts`: 新增 observability、memory、rag 相关 API 定义

---

## Phase 8: 数据迁移 & 启动流程

### Steps

1. 在 `main.py` `_auto_migrate()` 中新增 ALTER TABLE 语句:
   - `skills` 表: 添加 `behavior_type`, `required_tools`, `output_schema`, `validation_rules`, `pre_hooks`, `post_hooks`
   - `projects` 表: 添加 `agent_strategy`, `memory_enabled`, `rag_enabled`, `token_budget_daily`, `token_budget_monthly`

2. CREATE TABLE 新表 (在 `_auto_migrate()` 中 CREATE IF NOT EXISTS):
   - `vector_embeddings`, `memory_facts`, `memory_decisions`, `user_preferences`
   - `ai_traces`, `cost_tracking`, `project_budgets`

3. 启动流程更新 (lifespan):
   - `LLMClient` 初始化 (Provider 池)
   - `ToolRegistry` 初始化 (加载 builtin + DB + MCP)
   - `VectorIndex` 加载 (从 SQLite 读入内存)
   - `BackgroundIndexer` 启动 (首次全量索引)
   - `MemoryStore` 初始化
   - Agent 策略缓存加载

4. 更新 `requirements.txt`:
   - `numpy>=1.24.0` (向量运算)
   - 无其他新依赖 (保持轻量)

---

## 实施顺序 & 依赖关系

```
Phase 1 (Provider + LLM)  ──── 无依赖, 可立即开始
    │
Phase 2 (Tools)  ─────────── 依赖 Phase 1 (LLMClient 用于 embed)
    │
Phase 3 (Agents)  ────────── 依赖 Phase 1 + 2 (LLM + Tools)
    │
Phase 4 (Context + RAG + Memory) ── 依赖 Phase 1 (LLM for embedding + summarization)
    │
Phase 5 (Skills)  ────────── 依赖 Phase 3 + 4 (Agent + Context)
    │
Phase 6 (Observability)  ── 依赖 Phase 3 (Agent tracer hooks)
    │
Phase 7 (Frontend)  ─────── 依赖 Phase 3 + 6 (new events + API)
    │
Phase 8 (Migration)  ─────── 贯穿所有阶段 (每阶段添加自己的表/列)
```

---

## Verification Criteria

- **Phase 1**: 现有所有 AI 对话功能通过兼容桥接正常运行，`npm run build` 通过
- **Phase 2**: 工具调用结果与重构前一致，MCP 工具仍可正常连接和执行
- **Phase 3**: 对话发送 → 收到 SSE 事件流 → 工具调用 → 最终回复，与现有行为一致；新增 reflection 事件在前端可见
- **Phase 4**: 对话中 RAG 检索相关代码片段注入上下文；记忆跨会话保持
- **Phase 5**: 配置 `tool_routing` 类型的 Skill 后，AI 自动使用指定工具
- **Phase 6**: 对话完成后 `ai_traces` 表有完整调用链记录；成本 API 返回正确统计
- **Phase 7**: 前端 Plan 面板、Reflection 消息、成本看板正常渲染
- **Each phase**: `npm run build` 无报错、手动对话测试

---

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| AI 框架 | 内置，不用 LangChain/LlamaIndex | 保持零外部 AI 框架依赖，完全掌控行为 |
| 向量运算 | numpy cosine | faiss 需编译安装且 ARM 兼容差，numpy 足以应对 <100K chunks |
| 向量存储 | SQLite BLOB + 内存索引 | 与项目现有 SQLite 架构一致，无需新数据库 |
| 兼容方式 | 旧模块保留 thin wrapper | 避免一次性修改 20+ 个导入点 |
| Skill 默认行为 | behavior_type = "prompt_only" | 所有现有 Skill 自动 backward compatible |
| Agent 默认模式 | 讨论阶段 ReAct，实施阶段 Planning | 混合模式：workflow stage 驱动 Agent 策略选择 |
| 新增依赖 | 仅 numpy | 最小化依赖增长，TF-IDF fallback 避免强制要求 embedding API |

重构过程可以不考虑向后兼容性，直接重构现有模块，但为了降低风险和分阶段验证，可选择兼容桥接的方式。