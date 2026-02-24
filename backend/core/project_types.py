"""
项目类型定义

优先从 DB 工作流缓存读取, 回退到硬编码默认值。
所有外部代码继续通过 get_project_type() / get_role_for_status() 等函数访问,
内部数据源已透明切换为 DB-backed 工作流。

关系: Project.project_type → Workflow.name → stages + modules + ui_labels
"""

from typing import Dict, Any, List, Optional

# ======================== 硬编码 Fallback (DB 不可用时使用) ========================

_FALLBACK_PROJECT_TYPES: Dict[str, Dict[str, Any]] = {
    "requirement": {
        "name": "需求",
        "icon": "📋",
        "description": "产品需求迭代",
        "stages": [
            {"key": "draft", "label": "草稿", "status": "draft"},
            {"key": "discussing", "label": "讨论", "status": "discussing", "role": "需求分析"},
            {"key": "planned", "label": "定稿", "status": "planned"},
            {"key": "implementing", "label": "实施", "status": "implementing"},
            {"key": "reviewing", "label": "审查", "status": "reviewing", "role": "实现审查"},
            {"key": "deploying", "label": "部署", "status": "deploying"},
            {"key": "deployed", "label": "完成", "status": "deployed"},
        ],
        "ui_labels": {
            "project_noun": "需求",
            "create_title": "📋 新建需求",
            "create_placeholder": "简明描述需求目标",
            "description_placeholder": "详细描述需求背景和期望效果...",
            "output_noun": "需求规格书",
            "output_tab_label": "📋 设计稿",
            "finalize_action": "敲定方案",
            "discuss_tab_label": "💬 讨论 & 设计",
            "review_output_noun": "审查报告",
            "review_tab_label": "📋 审查报告",
            "review_finalize_action": "生成报告",
            "review_discuss_tab_label": "💬 审查",
        },
    },
    "bug": {
        "name": "缺陷",
        "icon": "🔍",
        "description": "Bug 问诊与修复",
        "stages": [
            {"key": "draft", "label": "报告", "status": "draft"},
            {"key": "discussing", "label": "问诊", "status": "discussing", "role": "Bug 问诊"},
            {"key": "planned", "label": "诊断书", "status": "planned"},
            {"key": "implementing", "label": "修复", "status": "implementing"},
            {"key": "reviewing", "label": "验证", "status": "reviewing", "role": "实现审查"},
            {"key": "deploying", "label": "部署", "status": "deploying"},
            {"key": "deployed", "label": "关闭", "status": "deployed"},
        ],
        "ui_labels": {
            "project_noun": "缺陷",
            "create_title": "🐛 新建缺陷",
            "create_placeholder": "简明描述 Bug 现象",
            "description_placeholder": "描述 Bug 的具体表现、出现场景...",
            "output_noun": "诊断书",
            "output_tab_label": "📋 诊断书",
            "finalize_action": "生成诊断书",
            "discuss_tab_label": "💬 问诊",
            "review_output_noun": "审查报告",
            "review_tab_label": "📋 审查报告",
            "review_finalize_action": "生成报告",
            "review_discuss_tab_label": "💬 验证",
        },
    },
}

# 默认项目类型
DEFAULT_PROJECT_TYPE = "requirement"

# 向后兼容: 外部代码可能直接 import PROJECT_TYPES
PROJECT_TYPES = _FALLBACK_PROJECT_TYPES


def _get_effective_types() -> Dict[str, Dict[str, Any]]:
    """优先从 DB 工作流缓存获取, 回退到硬编码"""
    try:
        from studio.backend.api.workflows import get_workflow_cache
        cache = get_workflow_cache()
        if cache:
            return cache
    except Exception:
        pass
    return _FALLBACK_PROJECT_TYPES


def get_project_type(type_key: str) -> Optional[Dict[str, Any]]:
    """获取项目类型配置 (优先 DB, 回退 hardcoded)"""
    return _get_effective_types().get(type_key)


def get_all_project_types() -> List[Dict[str, Any]]:
    """获取所有项目类型 (带 key)"""
    types = _get_effective_types()
    return [
        {"key": k, **v}
        for k, v in types.items()
    ]


def get_role_for_status(type_key: str, status: str) -> Optional[str]:
    """根据项目类型和当前状态, 返回该阶段对应的 role 名称 (无则返回 None)"""
    pt = _get_effective_types().get(type_key)
    if not pt:
        return None
    for stage in pt.get("stages", []):
        if stage.get("status") == status:
            return stage.get("role")
    return None


def get_stages(type_key: str) -> List[Dict[str, Any]]:
    """获取项目类型的阶段列表"""
    pt = _get_effective_types().get(type_key)
    if not pt:
        return []
    return pt.get("stages", [])


def get_ui_labels(type_key: str) -> Dict[str, str]:
    """获取项目类型的 UI 文案"""
    pt = _get_effective_types().get(type_key)
    if not pt:
        return {}
    return pt.get("ui_labels", {})


def get_modules(type_key: str) -> List[Dict[str, Any]]:
    """获取项目类型的模块列表 (仅 DB-backed 工作流有)"""
    pt = _get_effective_types().get(type_key)
    if not pt:
        return []
    return pt.get("modules", [])


def validate_stage_transition(type_key: str, current_status: str, new_status: str) -> tuple:
    """验证状态跳转是否合法。
    规则:
    - 只允许跳到当前阶段或下一阶段
    - 特殊允许: reviewing → discussing (迭代)
    - 相同状态跳转允许 (no-op)
    返回 (ok: bool, error_msg: str)
    """
    if current_status == new_status:
        return True, ""

    stages = get_stages(type_key)
    if not stages:
        # 未知类型，不限制
        return True, ""

    status_order = [s["status"] for s in stages]
    current_idx = status_order.index(current_status) if current_status in status_order else -1
    new_idx = status_order.index(new_status) if new_status in status_order else -1

    if current_idx < 0 or new_idx < 0:
        # 未知状态，不限制
        return True, ""

    # 允许前进一步
    if new_idx == current_idx + 1:
        return True, ""

    # 允许 reviewing → discussing (迭代)
    if current_status == "reviewing" and new_status == "discussing":
        return True, ""

    # 其他情况禁止
    current_label = stages[current_idx]["label"] if current_idx >= 0 else current_status
    new_label = stages[new_idx]["label"] if new_idx >= 0 else new_status
    return False, f"不能从「{current_label}」跳转到「{new_label}」，请先完成当前阶段"
