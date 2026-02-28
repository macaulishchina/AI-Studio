"""
Observability — 请求追踪器

为每次 AI 调用提供结构化的 trace 数据:
  - 请求 ID、模型、token 用量、延迟
  - 工具调用链路
  - 错误记录
  - 成本估算

数据存储到 SQLite (ai_traces 表) + 内存环形缓冲。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TraceType(str, Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    AGENT_RUN = "agent_run"
    EMBEDDING = "embedding"
    RAG_QUERY = "rag_query"
    ERROR = "error"


@dataclass
class TraceSpan:
    """单个追踪 span"""
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    trace_id: str = ""
    parent_id: str = ""
    trace_type: TraceType = TraceType.LLM_CALL
    name: str = ""
    model_id: str = ""
    project_id: str = ""

    # 时间
    start_time: float = 0.0
    end_time: float = 0.0

    # Token 用量
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # 成本 (USD cents)
    estimated_cost_cents: float = 0.0

    # 状态
    status: str = "ok"  # ok / error / timeout
    error_message: str = ""

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "type": self.trace_type.value,
            "name": self.name,
            "model_id": self.model_id,
            "project_id": self.project_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_cents": round(self.estimated_cost_cents, 4),
            "status": self.status,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


# ── 成本估算表 (每 1M tokens, USD) ──

MODEL_COSTS: Dict[str, Dict[str, float]] = {
    # GitHub Models / Azure OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o1": {"input": 15.00, "output": 60.00},
    "o1-mini": {"input": 1.10, "output": 4.40},
    "o3": {"input": 10.00, "output": 40.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
    # DeepSeek
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    # Qwen
    "qwen-plus": {"input": 0.80, "output": 2.00},
    # Copilot (free tier — no cost)
    "copilot:gpt-4o": {"input": 0, "output": 0},
    "copilot:claude-3.5-sonnet": {"input": 0, "output": 0},
}


def estimate_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """估算成本 (USD cents)"""
    # 精确匹配或前缀匹配
    costs = MODEL_COSTS.get(model_id)
    if not costs:
        for key, val in MODEL_COSTS.items():
            if model_id.startswith(key) or key in model_id:
                costs = val
                break
    if not costs:
        return 0.0

    cost_usd = (
        prompt_tokens / 1_000_000 * costs["input"]
        + completion_tokens / 1_000_000 * costs["output"]
    )
    return cost_usd * 100  # → cents


class Tracer:
    """
    请求追踪器

    维护内存环形缓冲 + 异步写入 SQLite。
    """

    def __init__(self, buffer_size: int = 1000):
        self._buffer: deque[TraceSpan] = deque(maxlen=buffer_size)
        self._active_spans: Dict[str, TraceSpan] = {}
        self._write_queue: asyncio.Queue[TraceSpan] = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None

    def start_span(
        self,
        trace_type: TraceType,
        name: str = "",
        trace_id: str = "",
        parent_id: str = "",
        model_id: str = "",
        project_id: str = "",
        metadata: Optional[dict] = None,
    ) -> TraceSpan:
        """开始一个新 span"""
        span = TraceSpan(
            trace_id=trace_id or uuid.uuid4().hex[:16],
            parent_id=parent_id,
            trace_type=trace_type,
            name=name,
            model_id=model_id,
            project_id=project_id,
            start_time=time.time(),
            metadata=metadata or {},
        )
        self._active_spans[span.span_id] = span
        return span

    def end_span(
        self,
        span: TraceSpan,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        status: str = "ok",
        error_message: str = "",
    ):
        """结束一个 span"""
        span.end_time = time.time()
        span.prompt_tokens = prompt_tokens
        span.completion_tokens = completion_tokens
        span.total_tokens = prompt_tokens + completion_tokens
        span.status = status
        span.error_message = error_message
        span.estimated_cost_cents = estimate_cost(
            span.model_id, prompt_tokens, completion_tokens
        )

        self._active_spans.pop(span.span_id, None)
        self._buffer.append(span)

        # 异步写入队列
        try:
            self._write_queue.put_nowait(span)
        except asyncio.QueueFull:
            pass

    def get_recent(self, limit: int = 50, project_id: Optional[str] = None) -> List[dict]:
        """获取最近的 trace"""
        spans = list(self._buffer)
        if project_id:
            spans = [s for s in spans if s.project_id == project_id]
        spans = spans[-limit:]
        return [s.to_dict() for s in reversed(spans)]

    def get_stats(self, project_id: Optional[str] = None) -> dict:
        """获取汇总统计"""
        spans = list(self._buffer)
        if project_id:
            spans = [s for s in spans if s.project_id == project_id]

        if not spans:
            return {
                "total_calls": 0,
                "total_tokens": 0,
                "total_cost_cents": 0,
                "avg_duration_ms": 0,
                "error_count": 0,
                "by_model": {},
            }

        total_tokens = sum(s.total_tokens for s in spans)
        total_cost = sum(s.estimated_cost_cents for s in spans)
        durations = [s.duration_ms for s in spans if s.duration_ms > 0]
        errors = sum(1 for s in spans if s.status == "error")

        by_model: Dict[str, dict] = {}
        for s in spans:
            if s.model_id not in by_model:
                by_model[s.model_id] = {"calls": 0, "tokens": 0, "cost_cents": 0}
            by_model[s.model_id]["calls"] += 1
            by_model[s.model_id]["tokens"] += s.total_tokens
            by_model[s.model_id]["cost_cents"] += s.estimated_cost_cents

        return {
            "total_calls": len(spans),
            "total_tokens": total_tokens,
            "total_cost_cents": round(total_cost, 2),
            "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else 0,
            "error_count": errors,
            "by_model": by_model,
        }

    async def start_writer(self):
        """启动后台写入任务"""
        if self._writer_task is not None:
            return
        self._writer_task = asyncio.create_task(self._write_loop())

    async def stop_writer(self):
        """停止后台写入"""
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None

    async def _write_loop(self):
        """后台写入循环"""
        while True:
            spans_to_write: List[TraceSpan] = []
            try:
                # 等待第一个
                span = await self._write_queue.get()
                spans_to_write.append(span)
                # 批量收集更多
                while not self._write_queue.empty() and len(spans_to_write) < 50:
                    spans_to_write.append(self._write_queue.get_nowait())
            except asyncio.CancelledError:
                break

            if spans_to_write:
                try:
                    await self._persist_spans(spans_to_write)
                except Exception as e:
                    logger.warning("Trace 写入失败: %s", e)

    async def _persist_spans(self, spans: List[TraceSpan]):
        """写入 SQLite"""
        try:
            from studio.backend.core.database import async_session_maker
            from sqlalchemy import text

            async with async_session_maker() as session:
                await session.execute(text("""
                    CREATE TABLE IF NOT EXISTS ai_traces (
                        span_id TEXT PRIMARY KEY,
                        trace_id TEXT,
                        parent_id TEXT,
                        trace_type TEXT,
                        name TEXT,
                        model_id TEXT,
                        project_id TEXT,
                        start_time REAL,
                        end_time REAL,
                        duration_ms REAL,
                        prompt_tokens INTEGER,
                        completion_tokens INTEGER,
                        total_tokens INTEGER,
                        estimated_cost_cents REAL,
                        status TEXT,
                        error_message TEXT,
                        metadata TEXT
                    )
                """))
                for span in spans:
                    await session.execute(
                        text("""INSERT OR REPLACE INTO ai_traces
                                (span_id, trace_id, parent_id, trace_type, name,
                                 model_id, project_id, start_time, end_time, duration_ms,
                                 prompt_tokens, completion_tokens, total_tokens,
                                 estimated_cost_cents, status, error_message, metadata)
                                VALUES (:sid, :tid, :pid, :tt, :name,
                                        :mid, :prid, :st, :et, :dur,
                                        :pt, :ct, :totalt,
                                        :cost, :status, :err, :meta)"""),
                        {
                            "sid": span.span_id,
                            "tid": span.trace_id,
                            "pid": span.parent_id,
                            "tt": span.trace_type.value,
                            "name": span.name,
                            "mid": span.model_id,
                            "prid": span.project_id,
                            "st": span.start_time,
                            "et": span.end_time,
                            "dur": span.duration_ms,
                            "pt": span.prompt_tokens,
                            "ct": span.completion_tokens,
                            "totalt": span.total_tokens,
                            "cost": span.estimated_cost_cents,
                            "status": span.status,
                            "err": span.error_message,
                            "meta": json.dumps(span.metadata),
                        },
                    )
                await session.commit()
        except Exception as e:
            logger.warning("Trace 持久化异常: %s", e)


# ── 全局单例 ──

_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    """获取全局 Tracer"""
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer
