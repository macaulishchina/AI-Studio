"""
Memory — MemoryService (v2)

记忆系统的唯一对外接口:
  - load_for_prompt(): 生成注入 system prompt 的记忆文本
  - extract_and_store(): 后台提取 + 存储
  - consolidate(): 去重合并
  - get_profile(): 用户画像
  - get_stats(): 统计
  - search(): 语义搜索
  - clear(): 清空用户记忆

所有方法内部检查 memory_enabled 配置。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from backend.models import MemoryType

logger = logging.getLogger(__name__)


class MemoryService:
    """记忆系统统一服务门面"""

    async def _is_enabled(self) -> bool:
        """检查记忆是否启用"""
        try:
            from backend.services.config_service import get_memory_config
            cfg = await get_memory_config()
            return cfg.get("memory_enabled", True)
        except Exception:
            return True  # 默认启用

    async def load_for_prompt(
        self,
        user_id: str,
        conversation_id: Optional[int] = None,
        max_items: int = 20,
    ) -> str:
        """
        生成可注入 system prompt 的记忆文本。

        返回格式化文本, 空字符串表示无记忆或已禁用。
        """
        if not await self._is_enabled():
            return ""

        from backend.ai.memory.store import get_memory_store
        store = get_memory_store()
        sections = []

        # L1: 当前对话记忆
        if conversation_id:
            conv_memories = await store.list_by_conversation(conversation_id, limit=10)
            if conv_memories:
                sections.append("### 当前对话记忆")
                for m in conv_memories:
                    label = m.memory_type.upper() if isinstance(m.memory_type, str) else m.memory_type
                    sections.append(f"- [{label}] {m.content}")

        # L2: 跨对话用户记忆
        user_memories = await store.list_by_user(user_id, limit=max_items)
        conv_ids = set()
        if conversation_id:
            conv_mems = await store.list_by_conversation(conversation_id, limit=50)
            conv_ids = {m.id for m in conv_mems}
        user_extra = [m for m in user_memories if m.id not in conv_ids]

        if user_extra:
            sections.append("### 用户画像 (跨对话)")
            for m in user_extra[:10]:
                label = m.memory_type.upper() if isinstance(m.memory_type, str) else m.memory_type
                sections.append(f"- [{label}] {m.content}")

        if not sections:
            return ""

        return (
            "## 长期记忆\n\n"
            "你拥有关于这位用户的以下记忆，请自然地利用这些信息提供个性化服务。\n\n"
            + "\n".join(sections)
        )

    async def extract_and_store(
        self,
        messages: List[dict],
        user_id: str,
        project_id: Optional[str] = None,
        conversation_id: Optional[int] = None,
    ) -> int:
        """
        后台提取记忆并存储。

        Returns:
            存储的记忆条数
        """
        if not await self._is_enabled():
            return 0

        # 检查自动提取开关
        try:
            from backend.services.config_service import get_memory_config
            cfg = await get_memory_config()
            if not cfg.get("memory_auto_extract", True):
                return 0
        except Exception:
            pass

        from backend.ai.memory.facts import get_fact_extractor
        extractor = get_fact_extractor()
        return await extractor.extract_from_messages(
            messages=messages,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
        )

    async def consolidate(self, user_id: str) -> int:
        """
        合并某用户的重复记忆。

        Returns:
            被删除的重复记忆数量
        """
        from backend.ai.memory.store import get_memory_store
        from backend.ai.memory.facts import _is_duplicate

        store = get_memory_store()
        all_memories = await store.list_by_user(user_id, limit=200)

        if len(all_memories) < 2:
            return 0

        kept: list = []
        to_remove: list[str] = []

        for m in all_memories:
            dup_idx = None
            for i, k in enumerate(kept):
                if _is_duplicate(m.content, k.content):
                    dup_idx = i
                    break

            if dup_idx is not None:
                existing = kept[dup_idx]
                if m.importance > existing.importance or (
                    m.importance == existing.importance
                    and (m.updated_at or 0) > (existing.updated_at or 0)
                ):
                    to_remove.append(existing.id)
                    kept[dup_idx] = m
                else:
                    to_remove.append(m.id)
                # 提升胜者重要性
                winner = kept[dup_idx]
                new_imp = min(winner.importance + 0.1, 1.0)
                await store.update_importance(winner.id, new_imp)
            else:
                kept.append(m)

        for mid in to_remove:
            await store.remove(mid)

        if to_remove:
            logger.info(f"用户 {user_id} 记忆合并: 移除 {len(to_remove)} 条重复")

        # 衰减旧记忆 + 裁剪超限
        try:
            from backend.services.config_service import get_memory_config
            cfg = await get_memory_config()
            decay_days = cfg.get("memory_decay_days", 30)
            max_per_user = cfg.get("memory_max_per_user", 500)
        except Exception:
            decay_days = 30
            max_per_user = 500

        await store.decay_old_memories(threshold_days=decay_days)
        await store.trim_by_user(user_id, max_count=max_per_user)

        return len(to_remove)

    async def get_profile(self, user_id: str) -> str:
        """生成用户画像摘要文本"""
        from backend.ai.memory.store import get_memory_store
        store = get_memory_store()
        all_memories = await store.list_by_user(user_id, limit=50)

        if not all_memories:
            return "暂无用户画像信息"

        grouped: dict = {}
        for m in all_memories:
            key = m.memory_type
            grouped.setdefault(key, []).append(m)

        parts = [f"用户: {user_id}", ""]
        type_labels = {
            "preference": "偏好",
            "fact": "已知事实",
            "decision": "决策记录",
            "profile": "画像",
            "episode": "事件摘要",
        }

        for mtype, items in grouped.items():
            label = type_labels.get(mtype, mtype)
            parts.append(f"### {label}")
            for item in items[:10]:
                parts.append(f"- {item.content} (重要性: {item.importance:.1f})")
            parts.append("")

        return "\n".join(parts)

    async def get_stats(self, user_id: str) -> dict:
        """获取用户记忆统计"""
        from backend.ai.memory.store import get_memory_store
        store = get_memory_store()
        total = await store.count(user_id=user_id)
        facts = await store.count(user_id=user_id, memory_type=MemoryType.FACT)
        decisions = await store.count(user_id=user_id, memory_type=MemoryType.DECISION)
        preferences = await store.count(user_id=user_id, memory_type=MemoryType.PREFERENCE)
        episodes = await store.count(user_id=user_id, memory_type=MemoryType.EPISODE)
        profiles = await store.count(user_id=user_id, memory_type=MemoryType.PROFILE)
        return {
            "total": total,
            "facts": facts,
            "decisions": decisions,
            "preferences": preferences,
            "episodes": episodes,
            "profiles": profiles,
        }

    async def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 10,
        memory_type: Optional[str] = None,
    ) -> list:
        """语义搜索记忆"""
        from backend.ai.memory.store import get_memory_store
        store = get_memory_store()
        return await store.search(
            query=query,
            user_id=user_id,
            memory_type=memory_type,
            top_k=top_k,
        )

    async def clear(self, user_id: str) -> int:
        """清空用户所有记忆"""
        from backend.ai.memory.store import get_memory_store
        store = get_memory_store()
        return await store.clear_user(user_id)


# ── 全局单例 ──

_memory_service: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
