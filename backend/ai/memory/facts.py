"""
Memory — 事实提取器 (v2)

从对话中自动提取结构化事实, 存入长期记忆。
- 可配置提取模型 (通过 memory config)
- 双向提取: user + assistant 消息
- 支持 5 种记忆类型: fact/decision/preference/episode/profile
- 去重: 归一化 + 模糊匹配 + 向量相似度
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import List, Optional

from backend.models import MemoryType

logger = logging.getLogger(__name__)

# 规则匹配模式 (作为 LLM 提取的 fallback)
FACT_PATTERNS = [
    (r"(?:我们|项目|系统)(?:使用|用了?|基于|采用)\s*(.+?)(?:框架|语言|数据库|技术|来)", "tech_stack"),
    (r"(.+?)\s*版本[是为]?\s*([\d.]+)", "version"),
    (r"(?:命名|名字|变量|函数|类).*(?:使用|用|采用)\s*(.+?)(?:风格|规范|方式)", "naming"),
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
    """从对话中提取事实 + 决策 + 偏好 + 事件 + 画像"""

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    async def extract_from_messages(
        self,
        messages: List[dict],
        user_id: str,
        project_id: Optional[str] = None,
        auto_store: bool = True,
        conversation_id: Optional[int] = None,
    ) -> int:
        """
        从消息列表中提取记忆项并存储。

        Args:
            messages: [{"role": "user"/"assistant", "content": "..."}]
            user_id: 必填, 关联用户
            project_id: 关联项目 (可选)
            auto_store: 是否自动存入 MemoryStore
            conversation_id: 关联对话

        Returns:
            存储的记忆条数
        """
        # 读取配置: 是否从 assistant 消息提取
        extract_assistant = True
        try:
            from backend.services.config_service import get_memory_config
            cfg = await get_memory_config()
            extract_assistant = cfg.get("memory_extract_assistant", True)
        except Exception:
            pass

        # 收集要提取的文本
        texts = []
        for m in messages:
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            role = m.get("role", "")
            if role == "user":
                texts.append(content)
            elif role == "assistant" and extract_assistant:
                texts.append(content)

        if not texts:
            return 0

        items: List[dict] = []  # dicts with content, memory_type, importance, tags

        # 1) 尝试 LLM 提取
        if self.use_llm:
            try:
                llm_items = await self._llm_extract(texts)
                items.extend(llm_items)
            except Exception as e:
                logger.warning("LLM 事实提取失败, 回退到规则匹配: %s", e)
                items.extend(self._rule_extract(texts))
        else:
            items.extend(self._rule_extract(texts))

        # 2) 去重 (批内)
        items = self._deduplicate_batch(items)

        # 3) 与已有记忆库去重
        if auto_store and items:
            items = await self._deduplicate_against_store(items, user_id)

        # 4) 存储
        stored = 0
        if auto_store and items:
            from backend.ai.memory.store import get_memory_store
            store = get_memory_store()
            for it in items:
                try:
                    await store.add(
                        content=it["content"],
                        memory_type=it["memory_type"],
                        user_id=user_id,
                        project_id=project_id,
                        conversation_id=conversation_id,
                        importance=it.get("importance", 0.5),
                        tags=it.get("tags", []),
                        source=it.get("source", "extraction"),
                    )
                    stored += 1
                except Exception as e:
                    logger.warning("存储记忆失败: %s", e)

        logger.info("提取了 %d 条记忆 (user=%s, conv=%s)", stored, user_id, conversation_id)
        return stored

    async def _get_extraction_model(self) -> str:
        """获取提取用模型 (配置 > 聊天默认 > hardcoded)"""
        try:
            from backend.services.config_service import get_memory_config, get_chat_default_model
            cfg = await get_memory_config()
            model = cfg.get("memory_extraction_model", "")
            if model:
                return model
            # fallback 到聊天默认模型
            chat_model = await get_chat_default_model()
            if chat_model:
                return chat_model
        except Exception:
            pass
        return "gpt-4o-mini"

    async def _llm_extract(self, texts: List[str]) -> List[dict]:
        """使用 LLM 提取结构化事实"""
        from backend.ai.llm import LLMClient

        client = LLMClient.get_instance()
        combined = "\n---\n".join(texts[-5:])
        model = await self._get_extraction_model()

        prompt = f"""从以下对话消息中提取关键信息。每行一条，格式: [类型] 内容
类型包括:
- FACT: 确定性事实 (技术栈、版本、架构等)
- DECISION: 已做出的决策
- PREFERENCE: 用户偏好或习惯
- EPISODE: 重要事件或里程碑摘要
- PROFILE: 用户画像信息 (身份、角色、专业领域)

只提取明确的、有价值的信息。如果没有可提取的信息，回复"无"。

对话消息:
{combined}

提取结果:"""

        try:
            result = await client.complete(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=0.1,
                max_tokens=500,
            )
            return self._parse_llm_output(result)
        except Exception as e:
            logger.warning("LLM 提取调用失败: %s", e)
            return []

    @staticmethod
    def _parse_llm_output(output: str) -> List[dict]:
        """解析 LLM 输出"""
        items = []
        if not output or "无" in output.strip()[:5]:
            return items

        type_map = {
            "FACT": (MemoryType.FACT, 0.5),
            "DECISION": (MemoryType.DECISION, 0.6),
            "PREFERENCE": (MemoryType.PREFERENCE, 0.5),
            "EPISODE": (MemoryType.EPISODE, 0.4),
            "PROFILE": (MemoryType.PROFILE, 0.7),
        }

        for line in output.strip().split("\n"):
            line = line.strip()
            m = re.match(r'\[(\w+)\]\s*(.+)', line)
            if not m:
                continue
            type_str, content = m.group(1), m.group(2).strip()
            mapping = type_map.get(type_str.upper())
            if not mapping or len(content) < 5:
                continue
            mtype, importance = mapping
            items.append({
                "content": content,
                "memory_type": mtype.value,
                "importance": importance,
                "source": "llm_extraction",
                "tags": [],
            })
        return items

    @staticmethod
    def _rule_extract(texts: List[str]) -> List[dict]:
        """规则匹配提取"""
        items = []
        combined = " ".join(texts)

        for pattern, tag in FACT_PATTERNS:
            for m in re.finditer(pattern, combined):
                content = m.group(1).strip() if m.lastindex else m.group(0).strip()
                if len(content) > 3:
                    items.append({
                        "content": content,
                        "memory_type": MemoryType.FACT.value,
                        "importance": 0.5,
                        "tags": [tag],
                        "source": "rule_extraction",
                    })

        for pattern, tag in DECISION_PATTERNS:
            for m in re.finditer(pattern, combined):
                content = m.group(1).strip()
                if len(content) > 3:
                    items.append({
                        "content": content,
                        "memory_type": MemoryType.DECISION.value,
                        "importance": 0.6,
                        "tags": [tag],
                        "source": "rule_extraction",
                    })

        for pattern, tag in PREFERENCE_PATTERNS:
            for m in re.finditer(pattern, combined):
                content = m.group(1).strip()
                if len(content) > 2:
                    items.append({
                        "content": content,
                        "memory_type": MemoryType.PREFERENCE.value,
                        "importance": 0.4,
                        "tags": [tag],
                        "source": "rule_extraction",
                    })

        return items

    @staticmethod
    def _deduplicate_batch(items: List[dict]) -> List[dict]:
        """批内去重"""
        result = []
        for item in items:
            if not any(_is_duplicate(item["content"], r["content"]) for r in result):
                result.append(item)
        return result

    @staticmethod
    async def _deduplicate_against_store(items: List[dict], user_id: str) -> List[dict]:
        """与已有记忆库去重 — 含向量相似度第五级"""
        from backend.ai.memory.store import get_memory_store
        store = get_memory_store()
        existing = await store.list_by_user(user_id, limit=100)

        if not existing:
            return items

        existing_texts = [m.content for m in existing]

        # 预计算 existing embeddings (如果有)
        existing_embeddings = [m.embedding for m in existing]

        result = []
        for item in items:
            # 文本去重
            if any(_is_duplicate(item["content"], et) for et in existing_texts):
                logger.debug("跳过重复记忆: %s", item["content"][:60])
                continue

            # 向量相似度去重 (cosine > 0.92)
            is_dup = False
            try:
                from backend.ai.rag.embeddings import get_embedding_service, cosine_similarity
                svc = get_embedding_service()
                item_emb = await svc.embed_text(item["content"])
                for ee in existing_embeddings:
                    if ee and cosine_similarity(item_emb, ee) > 0.92:
                        is_dup = True
                        break
            except Exception:
                pass

            if not is_dup:
                result.append(item)
            else:
                logger.debug("向量去重: %s", item["content"][:60])

        if len(result) < len(items):
            logger.info("跨存储去重: %d → %d 条", len(items), len(result))
        return result


# ── 通用去重工具 ──

def _normalize(text: str) -> str:
    """归一化: 去标点空格, 统一大小写, NFKC"""
    import unicodedata
    t = text.lower().strip()
    t = re.sub(r'[^\w]', '', t, flags=re.UNICODE)
    return unicodedata.normalize('NFKC', t)


def _is_duplicate(a: str, b: str, threshold: float = 0.70) -> bool:
    """
    判断两条文本是否为重复记忆:
    1. 归一化后完全相同
    2. 子串关系
    3. SequenceMatcher ≥ threshold
    4. 字符 bigram Jaccard ≥ threshold
    """
    from difflib import SequenceMatcher
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short in long:
        return True
    if SequenceMatcher(None, na, nb).ratio() >= threshold:
        return True
    if len(na) < 4 or len(nb) < 4:
        return False
    sa = {na[i:i+2] for i in range(len(na) - 1)}
    sb = {nb[i:i+2] for i in range(len(nb) - 1)}
    intersection = len(sa & sb)
    union = len(sa | sb)
    return (intersection / union) >= threshold if union else False


# ── 便捷函数 ──

_extractor: Optional[FactExtractor] = None


def get_fact_extractor() -> FactExtractor:
    global _extractor
    if _extractor is None:
        _extractor = FactExtractor()
    return _extractor
