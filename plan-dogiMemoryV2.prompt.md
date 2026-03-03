# Plan: Dogi 记忆系统 v2 — 推倒重建

**核心结论：不用 mem0，自研重建。** mem0 的核心价值（向量检索 + 去重 + 多用户命名空间）Dogi 已有基础设施可以复用（RAG embeddings 模块 + SQLite）。引入 mem0 意味着多一层 Qdrant/Chroma 依赖 + 一套独立的 LLM 抽象层，与 Dogi 的"SQLite 单文件 + 统一 Provider"哲学冲突。但我们**大量借鉴** mem0 和 LangMem 的设计理念。

我们借鉴的核心思想：
- **mem0**: 命名空间隔离 (`user_id` + `session_id`)、memory.add/search 极简 API、后台异步提取（background formation）、token 压缩
- **LangMem**: 三类记忆（Semantic Collection / Profile / Episodic）、热路径 vs 后台形成、Procedural memory（系统指令优化）

总改动量：~6 个后端文件重写 + 1 个新前端页面 + 2 个 API 路由修改。涉及的现有文件全部可以推翻重写，不需要保持向后兼容。

---

## Steps

### 阶段一：后端记忆核心重建 (P0)

**1. 新建 ORM 模型 `MemoryItem`** — `backend/models.py`

删除 `store.py` 中的原始 SQL 建表，改为正规 ORM 模型，纳入 `_auto_migrate()` 体系：

- 字段设计借鉴 mem0：`id`, `content`, `memory_type` (fact/decision/preference/episode/profile), `user_id` (必填，不再有 "user" fallback), `project_id` (可选), `conversation_id` (可选), `importance` (0~1), `embedding` (JSON, 存向量), `tags` (JSON array), `source` (extraction/manual/consolidation), `access_count`, `last_accessed`, `created_at`, `updated_at`, `metadata` (JSON)
- 加索引：`(user_id, memory_type)`, `(conversation_id)`, `(project_id, user_id)`
- 删掉 `_ensure_table()` 那套自建表逻辑

**2. 重写 `store.py` — 向量+关键词混合检索** — `backend/ai/memory/store.py`

彻底重写 `SQLiteMemoryStore`：

- `add()`: 写入 ORM + 自动调用 `embed_text()` 生成 embedding 存入 `embedding` 字段
- `search()`: **混合检索** — SQL LIKE 关键词匹配 + embedding 余弦相似度（用 numpy 计算），加权合并排序。不再是纯关键词 LIKE
- `search()` 排序公式：`score = 0.4 * vector_sim + 0.3 * keyword_hit + 0.2 * importance + 0.1 * recency_decay`（可调）
- 复用已有的 `backend/ai/rag/embeddings.py` 的 `embed_text()` 生成向量，零新增依赖
- `decay()` / `consolidate()` / `count()` 改用 ORM 查询（SQLAlchemy select/update）
- 删掉 `_row_to_item()` 那套兼容旧表的 hack
- `user_id` 参数变为**必填**，不再接受 None fallback

**3. 重写 `facts.py` — 可配置模型 + 双向提取** — `backend/ai/memory/facts.py`

- `_llm_extract()` 中的 `model="gpt-4o-mini"` → 改为动态读取 `await get_memory_config()` 返回的 `extraction_model`
- 提取范围：不再只处理 `role == "user"` 的消息。新增 `extract_from_assistant` 配置项，默认开启。助手消息中经常包含决策和事实总结
- 提取 prompt 优化：借鉴 mem0 的结构化提取模板，增加 `EPISODE` 类型（对话事件摘要）和 `PROFILE` 类型（用户画像片段）
- 去重逻辑保持现有的 `_is_duplicate()` 四级匹配（已经很好），增加向量相似度作为第五级（embedding cosine > 0.92 → 重复）
- `user_id` 变为**必填参数**

**4. 重写 `user_memory.py` — 统一记忆服务** — `backend/ai/memory/user_memory.py`

重命名为 `MemoryService`（或保持文件名但重构类名），成为记忆系统的唯一对外接口：

- `load_for_prompt(user_id, conversation_id?) → str`: 生成注入 system prompt 的记忆文本（替代散落在 task_runner.py 的 25 行内联代码）
- `extract_and_store(messages, user_id, conversation_id?) → int`: 后台提取+存储
- `consolidate(user_id) → int`: 去重合并
- `get_profile(user_id) → str`: 用户画像
- `get_stats(user_id) → dict`: 统计
- `search(query, user_id, top_k) → List[MemoryItem]`: 语义搜索
- 所有方法内部检查 `memory_enabled` 配置，关闭时直接返回空

**5. 新增记忆配置** — `backend/services/config_service.py`

新增 `MEMORY_CONFIG_KEYS` 集合 + `get_memory_config()` / `set_memory_config()` 函数：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `memory_enabled` | bool | `true` | 全局开关 |
| `memory_extraction_model` | str | `""` (用聊天默认模型) | 提取用模型 |
| `memory_consolidation_model` | str | `""` (同上) | 合并用模型 |
| `memory_auto_extract` | bool | `true` | 每次对话后自动提取 |
| `memory_extract_assistant` | bool | `true` | 是否从助手消息提取 |
| `memory_max_per_user` | int | `500` | 每用户记忆上限 |
| `memory_decay_days` | int | `30` | 未访问衰减天数 |
| `memory_auto_consolidate_hours` | int | `24` | 自动合并周期 (0=关闭) |

当 `memory_extraction_model` 为空时，fallback 到 `chat_default_model`，再 fallback 到 `gpt-4o-mini`。

**6. 记忆配置 API** — 扩展 `backend/api/memory.py`

- `GET /studio-api/memory/config` → 返回当前记忆配置
- `PUT /studio-api/memory/config` → 更新记忆配置
- 已有的 CRUD 端点保留但修正 `user_id` 解析逻辑（统一用 `username`）

---

### 阶段二：Pipeline 集成 (P0)

**7. 删除 `MemoryContextSource` 死代码** — `backend/ai/context/sources/memory.py`

这个文件从未被导入过。不恢复它（context source 架构对记忆来说过度设计），直接删掉。记忆注入统一走 `MemoryService.load_for_prompt()` → 传入 `build_dogi_context(memory_text=...)` 和 `build_project_context()`。

**8. 重构 `_execute_conversation()` 记忆集成** — `backend/services/task_runner.py` (~L1273)

删掉 25 行内联记忆加载代码，替换为：
```python
from backend.ai.memory.user_memory import MemoryService
memory_service = MemoryService()
memory_text = await memory_service.load_for_prompt(user_id=user_id, conversation_id=conversation_id)
```

后台提取同理：
```python
asyncio.create_task(memory_service.extract_and_store(recent_msgs, user_id=user_id, conversation_id=conv_id))
```

**9. 给 `_execute_discussion()` 补上记忆** — `backend/services/task_runner.py` (~L692)

当前该函数**完全没有记忆**。补上：
- 从 `rt.sender_name` 或 `project.created_by` 解析出 `user_id`
- 调用 `MemoryService.load_for_prompt()` 注入到 `build_project_context()` 的 extra_context
- 对话结束后同样跑 `extract_and_store()`

**10. `user_id` 全链路传递** — `backend/services/task_runner.py`

- `RunningTask` 新增 `user_id` 字段
- `start_conversation_task()` / `start_discussion_task()` 接收并传递 `user_id`
- 调用方（API 路由 `backend/api/discussion.py`, `backend/api/conversations.py`）从 JWT `get_current_user()` 获取 `username` 传入
- 不再使用 `nickname` 或 `"user"` 字符串

---

### 阶段三：前端记忆设置页 (P1)

**11. 新建 `MemorySettings.vue`** — `frontend/src/views/settings/MemorySettings.vue`

放在 AI服务 分组下，包含：
- 全局开关 (NSwitch)
- 提取模型选择器 (NSelect, 从 `/studio-api/models` 获取可用模型列表)
- 合并模型选择器 (NSelect)
- 自动提取开关
- 提取助手消息开关
- 每用户上限 (NInputNumber)
- 衰减天数 (NInputNumber)
- 自动合并周期 (NInputNumber)
- 记忆统计卡片 (总数/事实/决策/偏好)
- 手动合并按钮 + 清空按钮 (danger)

**12. 注册到 Settings.vue** — `frontend/src/views/Settings.vue`

在 `allSections` 的 `ai` 分组中添加：
```ts
{ key: 'memory', label: '🧠 模型记忆', shortLabel: '记忆', group: 'ai', groupLabel: 'AI服务', component: MemorySettings }
```

**13. 前端 API 补充** — `frontend/src/api/index.ts`

新增 `getMemoryConfig()` / `updateMemoryConfig()` 接口定义。

---

### 阶段四：自动化维护 (P2)

**14. 后台定时任务** — `backend/main.py`

在 lifespan 中启动一个轻量级 asyncio 周期任务：
- 每 N 小时（读 `memory_auto_consolidate_hours`）对所有有记忆的用户跑 `consolidate()` + `decay_old_memories()`
- 每用户记忆超过 `memory_max_per_user` 时自动裁剪最低 importance 的旧记忆

**15. 向量 embedding 自动生成** — `backend/ai/memory/store.py`

`add()` 时调用 `embed_text()` 生成 embedding，存入 `embedding` 字段。如果 embedding 服务不可用（如未配置 token），graceful degradation 到纯关键词搜索。

---

## Verification

- 创建两个用户分别对话 → 查 `memory_items` 表确认 `user_id` 隔离
- 在记忆设置页改成不同模型 → 查日志确认提取时使用了新模型
- 在项目讨论页对话 → 确认 SSE 收到 `memory_updated` 事件
- `GET /studio-api/memory?user_id=xxx` → 确认只返回该用户的记忆
- `python -m py_compile` 对每个修改文件做语法检查

---

## Decisions

- **不用 mem0**: Dogi 的 SQLite 单文件 + 统一 Provider 体系与 mem0 的 Qdrant + 独立 LLM 层冲突太大。我们借鉴思想，自研实现
- **删除 MemoryContextSource 而非恢复**: Context source 架构对记忆注入来说是过度封装，直接用 `MemoryService` → `memory_text` 参数更直观
- **`username` 作为 user_id**: 稳定唯一，不用 nickname
- **embedding 列存 JSON 而非 BLOB**: 便于调试和迁移，性能对 SQLite 规模够用
- **提取模型默认跟随聊天默认模型**: 零配置即可用，高级用户可单独设置轻量模型节省 token
