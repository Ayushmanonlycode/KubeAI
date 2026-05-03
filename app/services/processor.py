import asyncio
from collections import defaultdict, deque
from time import monotonic

from app.agents.detection import DetectionAgents
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.models import AnomalyEvent, MetricPoint
from app.core.state import RuntimeState
from app.services.correlation import CorrelationEngine
from app.services.gemini import GeminiReasoningEngine
from app.services.queue import MetricQueue
from app.services.storage import StorageEngine


class EventProcessor:
    def __init__(
        self,
        settings: Settings,
        queue: MetricQueue,
        storage: StorageEngine,
        correlation: CorrelationEngine,
        gemini: GeminiReasoningEngine,
        state: RuntimeState,
    ) -> None:
        self.queue = queue
        self.storage = storage
        self.correlation = correlation
        self.gemini = gemini
        self.state = state
        self.detectors = DetectionAgents(settings)
        self.recent_metrics_by_pod: dict[tuple[str, str], deque[MetricPoint]] = defaultdict(lambda: deque(maxlen=50))
        self.recent_events_by_pod: dict[tuple[str, str], deque[AnomalyEvent]] = defaultdict(lambda: deque(maxlen=20))
        self.logger = get_logger(__name__, "processor")

    async def run(self) -> None:
        while True:
            started = monotonic()
            try:
                metric = await self.queue.pop()
                self.state.queue_ok = True
                if metric is None:
                    continue
                key = (metric.namespace, metric.pod_name)
                self.recent_metrics_by_pod[key].append(metric)

                for anomaly in self.detectors.observe(metric):
                    self.state.anomalies.appendleft(anomaly)
                    self.recent_events_by_pod[key].append(anomaly)
                    await self.storage.insert_anomaly(anomaly)
                    edges = self.correlation.observe(anomaly)
                    insight = await self.gemini.analyze(
                        metric.pod_name,
                        metric.namespace,
                        list(self.recent_events_by_pod[key]),
                        list(self.recent_metrics_by_pod[key]),
                    )
                    self.state.insights.appendleft(insight)
                    await self.storage.insert_insight(insight)
                    self.logger.info(
                        "anomaly_processed",
                        extra={
                            "event": "anomaly_processed",
                            "status": "success",
                            "pod": metric.pod_name,
                            "metric_type": metric.metric_type.value,
                            "edges": len(edges),
                        },
                    )

                self.state.processed_count += 1
                self.state.total_processing_latency_ms += (monotonic() - started) * 1000
            except Exception as exc:
                self.state.queue_ok = False
                self.state.error_count += 1
                self.logger.error("processor_failed", extra={"event": "processor_failed", "status": "error", "error": str(exc)})
                await asyncio.sleep(2)
