"""
Memory — 事实提取器

从对话中自动提取结构化事实, 存入长期记忆。
使用 LLM 做事实抽取, 有 fallback 的规则匹配。
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import List, Optional

from studio.backend.ai.memory.store import MemoryItem, MemoryType, get_memory_store

logger = logging.getLogger(__name__)

# 规则匹配模式 (作为 LLM 提取的 fallback)
FACT_PATTERNS = [
    # 技术栈声明
    (r"(?:我们|项目|系统)(?:使用|用了?|基于|采用)\s*(.+?)(?:框架|语言|数据库|技术|来)", "tech_stack"),
    # 版本信息
    (r"(.+?)\s*版本[是为]?\s*([\d.]+)", "version"),
    # 命名约定
    (r"(?:命名|名字|变量|函数|类).*(?:使用|用|采用)\s*(.+?)(?:风格|规范|方式)", "naming"),
    # 架构声明
    (r"(?:架构|结构|设计).*(?:是|为|采用)\s*(.+?)(?:模式|架构|方式)", "architecture"),
]

DECISION_PATTERNS = [
    (r"(?:决定|确定|选定|采用|最终|选择)(?:了|使用)?\s*(.+?)(?:,|，|。|$)", "decision"),
    (r"(?:我们|就|那就)(?:用|选)\s*(.+?)(?:吧|了|$)", "decision"),
]

PREFERENCE_PATTERNS = [
    (r"(?:我|我们?)(?:喜欢|偏好|倾向|习惯)(?:用|使用)?\s*(.+?)(?:,|，|。|$)", "preference"),
    (r"(?:不要|别|避免)(?:用|使用)?\s*(.+?)(?:,|，|。|$)", "avoidance"),
]


class FactExtractor:
    """从对话中提取事实 + 决策 + 偏好"""

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    async def extract_from_messages(
        self,
        messages: List[dict],
        project_id: Optional[str] = None,
        auto_store: bool = True,
    ) -> List[MemoryItem]:
        """
        从消息列表中提取记忆项

        Args:
            messages: [{"role": "user"/"assistant", "content": "..."}]
            project_id: 关联的项目 ID
            auto_store: 是否自动存储

        Returns:
            提取的记忆项列表
        """
        # 只处理用户消息 (助手消息中的事实来源于用户)
        user_texts = [
            m["content"] for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]

        if not user_texts:
            return []

        items: List[MemoryItem] = []

        # 1) 尝试 LLM 提取
        if self.use_llm:
            try:
                llm_items = await self._llm_extract(user_texts, project_id)
                items.extend(llm_items)
            except Exception as e:
                logger.warning("LLM 事实提取失败, 回退到规则匹配: %s", e)
                items.extend(self._rule_extract(user_texts, project_id))
        else:
            items.extend(self._rule_extract(user_texts, project_id))

        # 2) 去重
        items = self._deduplicate(items)

        # 3) 自动存储
        if auto_store and items:
            store = get_memory_store()
            for item in items:
                try:
                    await store.add(item)
                except Exception as e:
                    logger.warning("存储记忆失败: %s", e)

        logger.info("提取了 %d 条记忆 (project=%s)", len(items), project_id)
        return items

    async def _llm_extract(
        self, texts: List[str], project_id: Optional[str]
    ) -> List[MemoryItem]:
        """使用 LLM 提取结构化事实"""
        from studio.backend.ai.llm import LLMClient

        client = LLMClient.get_instance()
        combined = "\n---\n".join(texts[-5:])  # 只取最近 5 条

        prompt = f"""从以下用户消息中提取关键信息。每行一条，格式: [类型] 内容
类型包括: FACT(事实), DECISION(决策), PREFERENCE(偏好)
只提取明确的、有价值的信息。如果没有，回复"无"。

用户消息:
{combined}

提取结果:"""

        try:
            result = await client.complete(
                messages=[{"role": "user", "content": prompt}],
                model_id=None,  # 使用默认模型
                temperature=0.1,
                max_tokens=500,
            )
            return self._parse_llm_output(result.content, project_id)
        except Exception as e:
            logger.warning("LLM 提取调用失败: %s", e)
            return []

    def _parse_llm_output(
        self, output: str, project_id: Optional[str]
    ) -> List[MemoryItem]:
        """解析 LLM 输出"""
        items = []
        if not output or "无" in output.strip()[:5]:
            return items

        type_map = {
            "FACT": MemoryType.FACT,
            "DECISION": MemoryType.DECISION,
            "PREFERENCE": MemoryType.PREFERENCE,
        }

        for line in output.strip().split("\n"):
            line = line.strip()
            m = re.match(r'\[(\w+)\]\s*(.+)', line)
            if not m:
                continue
            type_str, content = m.group(1), m.group(2).strip()
            mtype = type_map.get(type_str.upper())
            if not mtype or len(content) < 5:
                continue

            items.append(MemoryItem(
                id=uuid.uuid4().hex[:16],
                content=content,
                memory_type=mtype,
                project_id=project_id,
                importance=0.6,
                source="llm_extraction",
            ))
        return items

    def _rule_extract(
        self, texts: List[str], project_id: Optional[str]
    ) -> List[MemoryItem]:
        """规则匹配提取"""
        items = []
        combined = " ".join(texts)

        for pattern, tag in FACT_PATTERNS:
            for m in re.finditer(pattern, combined):
                content = m.group(1).strip() if m.lastindex else m.group(0).strip()
                if len(content) > 3:
                    items.append(MemoryItem(
                        id=uuid.uuid4().hex[:16],
                        content=content,
                        memory_type=MemoryType.FACT,
                        project_id=project_id,
                        importance=0.5,
                        tags=[tag],
                        source="rule_extraction",
                    ))

        for pattern, tag in DECISION_PATTERNS:
            for m in re.finditer(pattern, combined):
                content = m.group(1).strip()
                if len(content) > 3:
                    items.append(MemoryItem(
                        id=uuid.uuid4().hex[:16],
                        content=content,
                        memory_type=MemoryType.DECISION,
                        project_id=project_id,
                        importance=0.6,
                        tags=[tag],
                        source="rule_extraction",
                    ))

        for pattern, tag in PREFERENCE_PATTERNS:
            for m in re.finditer(pattern, combined):
                content = m.group(1).strip()
                if len(content) > 2:
                    items.append(MemoryItem(
                        id=uuid.uuid4().hex[:16],
                        content=content,
                        memory_type=MemoryType.PREFERENCE,
                        project_id=project_id,
                        importance=0.4,
                        tags=[tag],
                        source="rule_extraction",
                    ))

        return items

    @staticmethod
    def _deduplicate(items: List[MemoryItem]) -> List[MemoryItem]:
        """简单去重 (基于内容相似度)"""
        seen = set()
        result = []
        for item in items:
            key = item.content.lower().strip()[:50]
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result


# ── 便捷函数 ──

_extractor: Optional[FactExtractor] = None


def get_fact_extractor() -> FactExtractor:
    global _extractor
    if _extractor is None:
        _extractor = FactExtractor()
    return _extractor
