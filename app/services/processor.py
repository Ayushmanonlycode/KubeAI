import asyncio
from collections import defaultdict, deque
from time import monotonic

from app.agents.detection import DetectionAgents
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.models import AnomalyEvent, MetricPoint
from app.core.state import RuntimeState
from app.services.correlation import CorrelationEngine
from app.services.queue import MetricQueue
from app.services.storage import StorageEngine


class EventProcessor:
    """Consumes metrics from the Redis stream, runs detection, stores anomalies.

    Gemini insight generation is handled separately by ``InsightWorker``
    so that slow AI calls never block anomaly detection.
    """

    def __init__(
        self,
        settings: Settings,
        queue: MetricQueue,
        storage: StorageEngine,
        correlation: CorrelationEngine,
        state: RuntimeState,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.storage = storage
        self.correlation = correlation
        self.state = state
        self.detectors = DetectionAgents(settings)
        self.recent_metrics_by_pod: dict[tuple[str, str], deque[MetricPoint]] = defaultdict(lambda: deque(maxlen=50))
        self.recent_events_by_pod: dict[tuple[str, str], deque[AnomalyEvent]] = defaultdict(lambda: deque(maxlen=20))
        self.logger = get_logger(__name__, "processor")

    async def run(self) -> None:
        while True:
            started = monotonic()
            try:
                result = await self.queue.consume()
                if result is None:
                    continue
                msg_id, metric = result
                self.state.queue_ok = True

                key = (metric.namespace, metric.pod_name)
                self.recent_metrics_by_pod[key].append(metric)

                anomaly_batch: list[AnomalyEvent] = []
                for anomaly in self.detectors.observe(metric):
                    self.state.anomalies.appendleft(anomaly)
                    self.recent_events_by_pod[key].append(anomaly)
                    anomaly_batch.append(anomaly)
                    self.correlation.observe(anomaly)

                    # Enqueue insight generation (non-blocking)
                    await self.queue.publish_insight_request(
                        anomaly.pod_name,
                        anomaly.namespace,
                        list(self.recent_events_by_pod[key]),
                        list(self.recent_metrics_by_pod[key]),
                    )

                if anomaly_batch:
                    await self.storage.insert_anomalies(anomaly_batch)
                    self.logger.info(
                        "anomalies_detected",
                        extra={
                            "event": "anomalies_detected",
                            "status": "success",
                            "pod": metric.pod_name,
                            "count": len(anomaly_batch),
                        },
                    )

                # ACK only after successful processing
                await self.queue.ack(msg_id)

                self.state.processed_count += 1
                self.state.total_processing_latency_ms += (monotonic() - started) * 1000
            except Exception as exc:
                self.state.queue_ok = False
                self.state.error_count += 1
                self.logger.error("processor_failed", extra={"event": "processor_failed", "status": "error", "error": str(exc)})
                await asyncio.sleep(2)


class InsightWorker:
    """Consumes insight requests from a separate Redis stream and calls Gemini.

    Runs with a concurrency semaphore so that multiple Gemini calls
    can proceed in parallel without overwhelming the API.
    """

    def __init__(
        self,
        settings: Settings,
        queue: MetricQueue,
        storage: StorageEngine,
        gemini: "GeminiReasoningEngine",  # noqa: F821
        state: RuntimeState,
        max_concurrency: int = 3,
    ) -> None:
        self.queue = queue
        self.storage = storage
        self.gemini = gemini
        self.state = state
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.logger = get_logger(__name__, "insight-worker")

    async def run(self) -> None:
        while True:
            try:
                result = await self.queue.consume_insight_request()
                if result is None:
                    continue
                msg_id, request = result
                asyncio.create_task(self._process(msg_id, request))
            except Exception as exc:
                self.logger.error(
                    "insight_consume_failed",
                    extra={"event": "insight_consume_failed", "status": "error", "error": str(exc)},
                )
                await asyncio.sleep(2)

    async def _process(self, msg_id: str, request: dict) -> None:
        async with self.semaphore:
            try:
                from app.core.models import AnomalyEvent, MetricPoint

                pod = request["pod"]
                namespace = request["namespace"]
                events = [AnomalyEvent.model_validate(e) for e in request.get("events", [])]
                metrics = [MetricPoint.model_validate(m) for m in request.get("metrics", [])]

                insight = await self.gemini.analyze(pod, namespace, events, metrics)
                self.state.insights.appendleft(insight)
                await self.storage.insert_insight(insight)
                await self.queue.ack_insight(msg_id)

                self.logger.info(
                    "insight_generated",
                    extra={
                        "event": "insight_generated",
                        "status": "success",
                        "pod": pod,
                        "fallback": insight.fallback,
                    },
                )
            except Exception as exc:
                self.logger.error(
                    "insight_generation_failed",
                    extra={"event": "insight_generation_failed", "status": "error", "error": str(exc)},
                )
