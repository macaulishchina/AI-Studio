"""
Observability — 指标收集器

轻量级指标收集, 用于仪表盘展示:
  - 请求计数 / 错误率
  - 延迟分布
  - Token 消耗趋势
  - 模型使用分布
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class MetricPoint:
    """单个指标点"""
    timestamp: float
    value: float
    labels: Dict[str, str]


class MetricsCollector:
    """
    指标收集器

    维护滑动窗口的各类计数器和直方图。
    """

    def __init__(self, window_minutes: int = 60):
        self._window_seconds = window_minutes * 60
        # 计数器: name → deque of (timestamp, value, labels)
        self._counters: Dict[str, deque] = defaultdict(lambda: deque(maxlen=10000))
        # 直方图: name → deque of (timestamp, value)
        self._histograms: Dict[str, deque] = defaultdict(lambda: deque(maxlen=10000))

    def increment(self, name: str, value: float = 1.0, **labels):
        """递增计数器"""
        self._counters[name].append(MetricPoint(
            timestamp=time.time(), value=value, labels=labels,
        ))

    def observe(self, name: str, value: float, **labels):
        """记录直方图观测值 (如延迟)"""
        self._histograms[name].append(MetricPoint(
            timestamp=time.time(), value=value, labels=labels,
        ))

    def get_counter_total(self, name: str, since: float = 0, **label_filter) -> float:
        """获取计数器总值"""
        if name not in self._counters:
            return 0
        total = 0.0
        for point in self._counters[name]:
            if since and point.timestamp < since:
                continue
            if label_filter and not all(
                point.labels.get(k) == v for k, v in label_filter.items()
            ):
                continue
            total += point.value
        return total

    def get_histogram_stats(self, name: str, since: float = 0) -> dict:
        """获取直方图统计"""
        if name not in self._histograms:
            return {"count": 0, "avg": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}

        values = [
            p.value for p in self._histograms[name]
            if (not since or p.timestamp >= since)
        ]
        if not values:
            return {"count": 0, "avg": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}

        values.sort()
        n = len(values)
        return {
            "count": n,
            "avg": round(sum(values) / n, 2),
            "p50": values[n // 2],
            "p90": values[int(n * 0.9)],
            "p99": values[int(n * 0.99)],
            "max": values[-1],
        }

    def get_time_series(
        self, name: str, bucket_seconds: int = 60, since: float = 0
    ) -> List[dict]:
        """获取时间序列 (按固定间隔聚合)"""
        source = self._counters.get(name) or self._histograms.get(name)
        if not source:
            return []

        now = time.time()
        if not since:
            since = now - self._window_seconds

        # 按时间桶聚合
        buckets: Dict[int, list] = defaultdict(list)
        for point in source:
            if point.timestamp < since:
                continue
            bucket_key = int(point.timestamp // bucket_seconds) * bucket_seconds
            buckets[bucket_key].append(point.value)

        result = []
        for ts in sorted(buckets.keys()):
            vals = buckets[ts]
            result.append({
                "timestamp": ts,
                "count": len(vals),
                "sum": round(sum(vals), 2),
                "avg": round(sum(vals) / len(vals), 2),
            })
        return result

    def get_dashboard_data(self, project_id: Optional[str] = None) -> dict:
        """生成仪表盘数据"""
        now = time.time()
        since_1h = now - 3600
        since_24h = now - 86400

        return {
            "requests_1h": self.get_counter_total("ai_requests", since=since_1h),
            "requests_24h": self.get_counter_total("ai_requests", since=since_24h),
            "errors_1h": self.get_counter_total("ai_errors", since=since_1h),
            "tokens_1h": self.get_counter_total("tokens_used", since=since_1h),
            "tokens_24h": self.get_counter_total("tokens_used", since=since_24h),
            "cost_cents_24h": self.get_counter_total("cost_cents", since=since_24h),
            "latency_1h": self.get_histogram_stats("ai_latency_ms", since=since_1h),
            "tool_calls_1h": self.get_counter_total("tool_calls", since=since_1h),
            "requests_timeseries": self.get_time_series("ai_requests", 300, since_1h),
            "tokens_timeseries": self.get_time_series("tokens_used", 300, since_1h),
        }

    def cleanup(self, max_age_seconds: Optional[int] = None):
        """清理过期数据"""
        cutoff = time.time() - (max_age_seconds or self._window_seconds)
        for name in list(self._counters.keys()):
            while self._counters[name] and self._counters[name][0].timestamp < cutoff:
                self._counters[name].popleft()
        for name in list(self._histograms.keys()):
            while self._histograms[name] and self._histograms[name][0].timestamp < cutoff:
                self._histograms[name].popleft()


# ── 全局单例 ──

_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
