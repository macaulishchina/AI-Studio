"""
设计院 (Studio) - 项目上下文构建服务
构建 AI 讨论的 system prompt，使 AI 具备项目感知能力

支持自适应压缩: 根据模型上下文窗口大小调整 system prompt 内容量
  - 极小 (<16K):  仅包含角色定义 + 项目概况 + plan 格式
  - 中等 (16K-128K): + 目录结构 + 关键目录列表 + 少量代码摘要
  - 大型 (>128K):  + 完整关键文件内容 (原始行为)

通用设计 — 不包含任何特定项目的硬编码，通过自动发现和 Role 配置适配不同项目。
"""
import os
import logging
from typing import Optional, Union, Tuple, List

from studio.backend.core.config import settings
from studio.backend.core.token_utils import estimate_tokens

logger = logging.getLogger(__name__)

# ======================== 自动发现关键文件和目录 ========================

# 常见项目说明文件 (按优先级排序, 存在则读取)
_CANDIDATE_KEY_FILES = [
    "CLAUDE.md",
    "COPILOT.md",
    "README.md",
    "CONTRIBUTING.md",
    "docker-compose.yml",
    "docker-compose.yaml",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Makefile",
]

# 常见源码目录 (按优先级, 存在则列出内容)
_CANDIDATE_KEY_DIRS = [
    # Python
    "app/api", "app/services", "app/models", "app/schemas",
    "backend/app/api", "backend/app/services", "backend/app/schemas",
    "src",
    # JS/TS
    "frontend/src/views", "frontend/src/components", "frontend/src/stores",
    "src/views", "src/components", "src/stores", "src/pages",
    # Go
    "cmd", "internal", "pkg",
    # Java
    "src/main/java", "src/main/resources",
    # Rust
    "src/bin",
]

# 自动发现的最大文件/目录数
_MAX_KEY_FILES = 8
_MAX_KEY_DIRS = 8


def discover_key_files(workspace: str) -> List[str]:
    """自动发现工作区中的关键文件 (存在 → 纳入)"""
    found = []
    for f in _CANDIDATE_KEY_FILES:
        if os.path.isfile(os.path.join(workspace, f)):
            found.append(f)
            if len(found) >= _MAX_KEY_FILES:
                break
    return found


def discover_key_dirs(workspace: str) -> List[str]:
    """自动发现工作区中的关键目录 (存在 → 纳入)"""
    found = []
    for d in _CANDIDATE_KEY_DIRS:
        if os.path.isdir(os.path.join(workspace, d)):
            found.append(d)
            if len(found) >= _MAX_KEY_DIRS:
                break
    return found

# ======================== Legacy 硬编码 Prompts ========================
# 兼容无 Role 配置的旧项目, 新项目应通过 Role 配置

LEGACY_ROLE_PROMPT = """你是一位资深产品经理和需求分析师，正在「设计院」中和用户讨论一个产品需求。

## 核心原则：需求探讨优先，实现细节靠后

你的首要任务是帮助用户把需求想清楚、说明白，而不是急于给出技术方案。

### 对话策略
1. **主动提问** — 用户描述需求后，立即用 `ask_user` 工具提出 3-5 个关键问题来澄清需求。不要等用户问你，你应该主动追问。
2. **聚焦「做什么」** — 讨论应围绕：用户故事、交互流程、业务规则、边界条件、优先级。避免主动讨论技术实现细节（数据库设计、API 路径等），除非用户明确要求。
3. **连续提问** — 如果一轮回答后仍有不明确的地方，继续用 `ask_user` 追问。宁可多问几轮，也不要带着模糊需求就敲定方案。
4. **总结确认** — 每轮问答后，简要总结你对需求的理解，让用户确认或纠正。
5. **循循善诱** — 帮助用户发现他们没想到的需求场景，如：异常流程、权限控制、数据一致性、并发场景。

### ⚠️ 绝对禁止的行为
- **禁止"预告式回复"**：不要说"好的，让我问几个问题："、"让我继续问…"然后就停止。如果你想提问，必须在**同一次回复中直接调用 `ask_user` 工具**。
- **禁止等待用户许可才提问**：不要说"需要我继续问吗？"——直接调用 `ask_user` 提问。
- **禁止无工具的纯确认回复**：不要用纯文字说"让我确认一下"然后停下来等用户回复。

### 什么时候讨论技术
- ✅ 用户主动问"这个用什么技术实现"时
- ✅ 需要查看代码来理解现有功能时
- ✅ 技术约束会影响需求可行性时（如实时推送需要 WebSocket）
- ❌ 不要主动建议数据库表结构、API 设计、组件拆分等
- ❌ 不要在用户只描述了大概想法时就给出完整技术方案

## 项目概况
此信息将根据工作区内容自动提供。请参考下方「项目结构」和「关键文件摘要」了解项目技术栈和架构。"""

LEGACY_FINALIZATION_PROMPT = """## 关于敲定方案
当用户说"敲定"时，系统会自动基于讨论历史生成需求规格书（Plan）。
你不需要在对话中输出 Plan 格式，只需确保讨论充分、需求明确即可。
在敲定之前，你应该主动确认：所有关键需求是否都已讨论清楚。"""

LEGACY_OUTPUT_GENERATION_PROMPT = """基于以下讨论内容，生成一份结构化的 **需求规格书（Plan）**。

## 写作原则

1. **聚焦「做什么」而非「怎么做」**：详细描述功能需求、业务规则、用户交互流程、边界条件、验收标准。不要给出具体的技术实现方案（如数据库表结构、API 路径设计、组件拆分方式），除非用户在讨论中明确要求了特定实现方式。
2. **保留用户的明确技术决策**：如果用户在讨论中主动提出了技术选型、架构约束或实现偏好，必须原样保留并标注为「用户指定」。
3. **需求要可验证**：每个功能点应有明确的完成标准，让实现者能判断"做到了没有"。
4. **消除歧义**：对讨论中模糊或有多种理解的地方，选择最合理的解释并明确写出，或标注为「待确认」。
5. **不要添加臆测**：严格基于讨论内容，不添加讨论中未涉及的功能或技术假设。

## 输出格式

### 项目概述
一段话描述项目目标和核心价值。

### 功能需求
按优先级分组，每个功能包含：
- **功能名称**
- **用户故事**: 作为 [角色]，我希望 [做什么]，以便 [达到什么目的]
- **详细描述**: 具体的交互流程、业务规则
- **边界条件**: 异常情况如何处理
- **验收标准**: 可检验的完成条件列表

### 非功能需求
性能、安全、兼容性等约束（仅包含讨论中提及的）。

### 用户指定的技术约束
仅列出用户在讨论中**主动要求**的技术决策（如指定某框架、某种数据格式等）。如果没有，写「无特定技术约束，由实现者自行决定最佳方案」。

### 待确认事项
讨论中未完全明确的问题。

---

讨论内容：
{discussion_summary}

请直接输出需求规格书内容（不需要代码块包裹）:"""

# 反伪造顶层指令 — 插入到 system prompt 最前面
ANTI_FABRICATION_HEADER = """## ⚠️ 核心规则：禁止伪造工具执行

你拥有可调用的工具（function calling / tool_call），包括 run_command、read_file、search_text 等。

**最重要的规则：**
- 当用户要求你执行命令时，你 **必须** 通过 tool_call 调用 `run_command` 工具。
- **绝对禁止** 在文本回复中编造"已执行"、"执行结果"等内容。你没有能力在文本中执行命令，只有 tool_call 才能真正执行。
- 如果你在文本中写了"已执行 xxx 命令"但没有发起 tool_call，那就是 **欺骗用户**，这是严重违规。
- 同理，不要在文本中伪装读取了文件内容。查看文件必须调用 `read_file` 工具。

**正确做法示例：**
用户说"删除 /tmp/3.txt" → 你调用 tool_call: `run_command({"command": "rm /tmp/3.txt"})`
用户说"查看 main.py 内容" → 你调用 tool_call: `read_file({"path": "main.py"})`

**错误做法（严格禁止）：**
用户说"删除 /tmp/3.txt" → 你在文本中写："已执行 rm /tmp/3.txt，文件已删除" ← 这是伪造！你根本没有执行！
"""

DEFAULT_TOOL_STRATEGY = """## 工具使用策略

### 重要原则
- **直接调用工具，不要描述意图**。不要说"让我查看一下…"、"让我问几个问题…"然后停止——直接调用对应工具。
- 每次回复中，要么调用工具，要么输出有实际内容的文本。**不要输出空响应。**
- **禁止"预告式"回复**：绝对不要输出类似"好的，让我继续问几个问题："这样的纯文本然后就停止。如果你想提问，必须在同一次回复中直接调用 `ask_user` 工具。
- **⚠️ 当用户要求执行命令时，必须通过 tool_call 调用 run_command，绝对不能在文本中编造执行结果。**

### ask_user — 主动提问澄清（最重要的工具）
- 用户描述内容后，**立即**用 `ask_user` 提出澄清问题
- **永远直接调用工具提问**：想问问题时，直接调用 `ask_user`。不要先用文字说"我来问几个问题"或"让我确认一下"然后等用户回复——这会浪费一轮对话
- 你可以在调用 `ask_user` 的同时附带简短的文字内容（如总结、说明），但**文字和工具调用必须在同一次回复中**
- 每个问题附带 2-5 个选项（对象格式：label + 可选 description + recommended 标记）
- **按推荐度排序**: 最推荐的选项放在最前面，设置 `recommended: true`
- **单选/多选**: 通过 `type: "single"` 或 `type: "multi"` 控制
- 单选题：最后一项通常是"其他（请说明）"自定义选项，严格几选一可省略
- 多选题：适用于允许组合的场景（如：需要支持哪些功能？）
- 调用 `ask_user` 后，你**必须停止输出**等待用户回答。不要自行假设答案继续推进
- 可以一次提多个问题，也可以分多轮逐步深入
- 问题应聚焦于：用户场景、业务规则、边界条件、优先级、兼容性

### 代码查看工具（按需使用）
1. **先搜后读** — 用 search_text 定位（返回文件路径 + 行号），再用 read_file 的 start_line 跳转精确读取
2. **缩小范围** — search_text 务必指定 include_pattern（如 `*.py`、`*.vue`）
3. **一次读够** — read_file 最多 200 行，小文件直接一次读完
4. **不要重复** — 已读过的内容不要再次读取
5. **先概览后细节** — 先 get_file_tree 了解结构，再针对性查看
- **当用户询问代码实现细节时，必须先用工具查看相关源码，再回答**。不要猜测。
- **绝对禁止伪造工具调用**：不要在回复文本中伪装执行了命令或读取了文件，你必须通过实际的 function calling / tool_call 来使用工具。如果用户让你执行命令，你必须调用 `run_command` 工具，绝不可只在文本中输出“执行结果”。
- 使用中文回答"""


def get_tree(path: str, max_depth: int = 3, prefix: str = "", current_depth: int = 0) -> str:
    """生成目录树字符串"""
    if current_depth >= max_depth:
        return ""

    result = []
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return ""

    # 过滤掉不需要的目录
    skip = {"node_modules", "__pycache__", ".git", ".venv", "venv", "dist", ".claude",
            "studio-data", "data", ".idea", ".vscode"}
    entries = [e for e in entries if e not in skip]

    for i, entry in enumerate(entries):
        full_path = os.path.join(path, entry)
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        result.append(f"{prefix}{connector}{entry}")

        if os.path.isdir(full_path):
            extension = "    " if is_last else "│   "
            subtree = get_tree(full_path, max_depth, prefix + extension, current_depth + 1)
            if subtree:
                result.append(subtree)

    return "\n".join(result)


def read_file_safe(filepath: str, max_lines: int = 200) -> str:
    """安全读取文件, 截断过长内容"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            content = "".join(lines[:max_lines])
            content += f"\n... (截断, 共 {len(lines)} 行)"
        else:
            content = "".join(lines)
        return content
    except Exception as e:
        return f"(读取失败: {e})"


def list_dir_files(dirpath: str) -> str:
    """列出目录中的文件"""
    try:
        entries = sorted(os.listdir(dirpath))
        entries = [e for e in entries if not e.startswith("__")]
        return ", ".join(entries)
    except Exception:
        return "(目录不存在)"


def build_project_context(
    role=None,
    extra_context: str = "",
    budget_tokens: int = 0,
    return_sections: bool = False,
    tool_permissions: set = None,
    ui_labels_override: dict = None,
    skills: list = None,
) -> Union[str, Tuple[str, List]]:
    """
    构建项目上下文用于 AI system prompt

    Args:
        role: Role ORM 对象 (None = 使用 legacy 硬编码)
        extra_context: 额外上下文 (需求标题/描述等)
        budget_tokens: system prompt token 预算 (0 = 不限制, 使用最大内容)
        return_sections: 是否额外返回各段明细 (用于前端上下文检查器)
        tool_permissions: 项目工具权限集合 (用于条件化工具策略)
        skills: Skill ORM 对象列表 — 活跃技能, 注入 instruction_prompt 到上下文

    Returns:
        str (when return_sections=False)
        (str, list) (when return_sections=True)
            list 中每项: {"name": "段名", "tokens": 123, "content": "...", "children": [...]}
    """
    ws = settings.workspace_path

    # 根据 budget 决定内容粒度
    level = "full"  # full / medium / minimal
    if 0 < budget_tokens <= 8000:
        level = "minimal"
    elif 0 < budget_tokens <= 32000:
        level = "medium"

    # 自动发现关键文件和目录
    key_files = discover_key_files(ws)
    key_dirs = discover_key_dirs(ws)

    # 项目结构 (medium + full)
    tree = ""
    if level != "minimal":
        tree = get_tree(ws, max_depth=2)

    # 关键文件 (full only)
    key_file_contents = []
    if level == "full":
        for f in key_files:
            fp = os.path.join(ws, f)
            if os.path.exists(fp):
                # 项目说明文件给更多行数
                max_lines = 300 if f.upper().endswith(".md") else 100
                content = read_file_safe(fp, max_lines=max_lines)
                key_file_contents.append((f, content, f"### {f}\n```\n{content}\n```"))
    elif level == "medium":
        # medium 只读 .md 说明文件和配置文件
        for f in key_files:
            if f.upper().endswith(".md") or f in ("docker-compose.yml", "docker-compose.yaml", "package.json"):
                fp = os.path.join(ws, f)
                if os.path.exists(fp):
                    content = read_file_safe(fp, max_lines=50)
                    key_file_contents.append((f, content, f"### {f}\n```\n{content}\n```"))

    # 关键目录 (medium + full)
    key_dir_contents = []
    if level != "minimal":
        for d in key_dirs:
            dp = os.path.join(ws, d)
            if os.path.isdir(dp):
                key_dir_contents.append(f"- `{d}/`: {list_dir_files(dp)}")

    # ---- 从 Role 或 legacy 获取 prompts ----
    if role:
        role_text = role.role_prompt or ""
        strategy_text = role.strategy_prompt or ""
        finalization_text = role.finalization_prompt or ""
        tool_strategy_text = role.tool_strategy_prompt or ""
    else:
        role_text = LEGACY_ROLE_PROMPT
        strategy_text = ""  # legacy 把 strategy 合并在 role_text 里了
        finalization_text = LEGACY_FINALIZATION_PROMPT
        tool_strategy_text = ""

    # 如果 role 没有自定义工具策略, 使用系统默认
    if not tool_strategy_text.strip():
        tool_strategy_text = DEFAULT_TOOL_STRATEGY

    # 根据 tool_permissions 条件修改工具策略
    _perms = tool_permissions or set()
    if _perms and "ask_user" not in _perms:
        # ask_user 未开启: 替换提问工具段落, 告知 AI 用文本提问
        tool_strategy_text = tool_strategy_text.replace(
            "### ask_user — 主动提问澄清（最重要的工具）",
            "### 提问方式（ask_user 工具未开启）"
        )
        # 在工具策略最前面追加提示
        ask_disabled_notice = (
            "⚠️ **ask_user 工具当前未开启**。\n"
            "- 如需向用户提问，请直接用**普通文本**提出问题和选项，不要尝试调用 ask_user 工具。\n"
            "- 用 Markdown 列表列出选项，标注推荐项。\n\n"
        )
        tool_strategy_text = ask_disabled_notice + tool_strategy_text

    # ask_user 是唯一开启的工具: 移除代码工具段落, 强调 ask_user
    _code_tool_perms = _perms - {"ask_user"}
    if _perms and "ask_user" in _perms and not _code_tool_perms:
        # 替换代码查看工具段落
        tool_strategy_text = tool_strategy_text.replace(
            "### 代码查看工具（按需使用）",
            "### 代码查看工具（未开启）"
        )
        ask_only_notice = (
            "\n\n⚠️ **当前仅开启了 ask_user 工具，代码查看工具均未开启。**\n"
            "- 你**必须积极使用 `ask_user` 工具**向用户提问，澄清需求细节。\n"
            "- 不要尝试调用 read_file、search_text、get_file_tree 等工具——它们不可用。\n"
            "- 每次回复都应包含一个 `ask_user` 调用来推进需求讨论。\n"
            "- 聚焦于：用户场景、业务规则、边界条件、优先级、技术选型。\n"
        )
        tool_strategy_text = tool_strategy_text + ask_only_notice

    # ---- 构建各段 (带跟踪) ----
    named_parts = []

    # 反伪造顶层指令 — 放在最前面, 确保模型优先看到
    if _perms and any(p in _perms for p in ("execute_readonly_command", "execute_command")):
        named_parts.append(("核心规则", ANTI_FABRICATION_HEADER))

    # 角色 + 策略: role 模式分段, legacy 模式合并
    if role:
        named_parts.append(("角色定义", role_text))
        if strategy_text:
            named_parts.append(("对话策略", strategy_text))
    else:
        named_parts.append(("角色定义 & 对话策略", role_text))

    # ---- 技能指令注入 ----
    if skills:
        skill_sections = []
        for sk in skills:
            section = f"### {sk.icon} {sk.name}\n{sk.instruction_prompt}"
            if sk.output_format:
                section += f"\n\n**输出格式:**\n{sk.output_format}"
            if sk.constraints:
                constraints_text = "\n".join(f"- {c}" for c in sk.constraints)
                section += f"\n\n**约束条件:**\n{constraints_text}"
            skill_sections.append(section)
        skill_block = "## 活跃技能\n\n以下技能定义了你在本次对话中应具备的专项能力。请在适当时机运用这些技能方法论。\n\n" + "\n\n---\n\n".join(skill_sections)
        named_parts.append(("活跃技能", skill_block))

    if tree:
        tree_text = f"## 项目结构\n```\n{tree}\n```"
        named_parts.append(("项目结构", tree_text))

    if key_dir_contents:
        dir_text = f"## 关键目录内容\n{chr(10).join(key_dir_contents)}"
        named_parts.append(("关键目录", dir_text))

    if key_file_contents:
        files_text = f"## 关键文件内容摘要\n{chr(10).join(item[2] for item in key_file_contents)}"
        named_parts.append(("关键文件摘要", files_text))

    if finalization_text:
        named_parts.append(("敲定方案提示", finalization_text))

    if extra_context:
        label = "项目上下文"
        _uilabels = ui_labels_override or (role.ui_labels if role and role.ui_labels else None) or {}
        if _uilabels:
            noun = _uilabels.get("project_noun", "需求")
            label = f"{noun}上下文"
        named_parts.append((label, extra_context))

    named_parts.append(("工具使用策略", tool_strategy_text))

    # 组装最终 prompt
    parts_text = [text for (_, text) in named_parts]
    context = "\n\n".join(parts_text)

    # 最终 token 检查: 如果仍然超预算则截断
    if budget_tokens > 0:
        current_tokens = estimate_tokens(context)
        if current_tokens > budget_tokens:
            logger.warning(
                f"System prompt ({current_tokens} tokens) 超出预算 ({budget_tokens}),"
                f" 级别={level}, 进行截断"
            )
            from studio.backend.core.token_utils import truncate_text
            context = truncate_text(context, budget_tokens)

    if not return_sections:
        return context

    # 构建分段明细树
    sections = []
    for name, text in named_parts:
        section = {"name": name, "tokens": estimate_tokens(text), "content": text}
        # 为"关键文件摘要"段添加子节点 (每个文件)
        if name == "关键文件摘要" and key_file_contents:
            children = []
            for fname, fcontent, _ in key_file_contents:
                children.append({
                    "name": fname,
                    "tokens": estimate_tokens(fcontent),
                    "content": fcontent,
                })
            section["children"] = children
        sections.append(section)

    return context, sections


def build_plan_generation_prompt(discussion_summary: str, role=None) -> str:
    """构建从讨论生成产出文档的 prompt, 根据 role 配置动态选择模板"""
    if role and role.output_generation_prompt:
        return role.output_generation_prompt.replace("{discussion_summary}", discussion_summary)
    # legacy fallback
    return LEGACY_OUTPUT_GENERATION_PROMPT.replace("{discussion_summary}", discussion_summary)
