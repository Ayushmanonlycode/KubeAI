from collections import deque
from dataclasses import dataclass, field
from time import monotonic

from app.core.models import AnomalyEvent, Insight, MetricPoint


@dataclass
class RuntimeState:
    collector_ok: bool = False
    collector_last_error: str | None = None
    gemini_ok: bool = False
    gemini_last_error: str | None = None
    queue_ok: bool = False
    database_ok: bool = False
    error_count: int = 0
    processed_count: int = 0
    total_processing_latency_ms: float = 0.0
    total_gemini_latency_ms: float = 0.0
    gemini_calls: int = 0
    started_at: float = field(default_factory=monotonic)
    recent_metrics: deque[MetricPoint] = field(default_factory=lambda: deque(maxlen=100))
    anomalies: deque[AnomalyEvent] = field(default_factory=lambda: deque(maxlen=500))
    insights: deque[Insight] = field(default_factory=lambda: deque(maxlen=100))

    @property
    def avg_processing_latency_ms(self) -> float:
        if self.processed_count == 0:
            return 0.0
        return self.total_processing_latency_ms / self.processed_count

    @property
    def avg_gemini_latency_ms(self) -> float:
        if self.gemini_calls == 0:
            return 0.0
        return self.total_gemini_latency_ms / self.gemini_calls

    @property
    def error_rate(self) -> float:
        elapsed = max(monotonic() - self.started_at, 1.0)
        return self.error_count / elapsed
