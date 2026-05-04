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

    Drains requests in short intervals, merges repeated pod anomalies into a
    single analysis, and only calls Gemini for high-priority batches.
    """

    def __init__(
        self,
        settings: Settings,
        queue: MetricQueue,
        storage: StorageEngine,
        gemini: "GeminiReasoningEngine",  # noqa: F821
        state: RuntimeState,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.storage = storage
        self.gemini = gemini
        self.state = state
        self.logger = get_logger(__name__, "insight-worker")

    async def run(self) -> None:
        while True:
            try:
                requests = await self._collect_batch()
                if not requests:
                    continue
                await self._process_batch(requests)
            except Exception as exc:
                self.logger.error(
                    "insight_consume_failed",
                    extra={"event": "insight_consume_failed", "status": "error", "error": str(exc)},
                )
                await asyncio.sleep(2)

    async def _collect_batch(self) -> list[tuple[str, dict]]:
        batch = await self.queue.consume_insight_requests(self.settings.insight_batch_size, timeout_ms=2000)
        if not batch:
            return []

        deadline = monotonic() + self.settings.insight_batch_window_seconds
        while len(batch) < self.settings.insight_batch_size:
            remaining_ms = int(max(0.0, deadline - monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            more = await self.queue.consume_insight_requests(
                self.settings.insight_batch_size - len(batch),
                timeout_ms=min(remaining_ms, 1000),
            )
            if more:
                batch.extend(more)
        return batch

    async def _process_batch(self, requests: list[tuple[str, dict]]) -> None:
        from app.core.models import AnomalyEvent, MetricPoint

        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for msg_id, request in requests:
            pod = request["pod"]
            namespace = request["namespace"]
            key = (namespace, pod)
            group = grouped.setdefault(key, {"msg_ids": [], "events": [], "metrics": []})
            group["msg_ids"].append(msg_id)  # type: ignore[union-attr]
            group["events"].extend(AnomalyEvent.model_validate(e) for e in request.get("events", []))  # type: ignore[union-attr]
            group["metrics"].extend(MetricPoint.model_validate(m) for m in request.get("metrics", []))  # type: ignore[union-attr]

        for (namespace, pod), group in grouped.items():
            try:
                msg_ids = group["msg_ids"]  # type: ignore[assignment]
                events = self._dedupe_events(group["events"])  # type: ignore[arg-type]
                metrics = self._dedupe_metrics(group["metrics"])  # type: ignore[arg-type]

                if self._should_call_ai(events):
                    insight = await self.gemini.analyze(pod, namespace, events, metrics)
                else:
                    insight = self.gemini.rules_insight(pod, namespace, events)

                self.state.insights.appendleft(insight)
                await self.storage.insert_insight(insight)
                await self.queue.ack_insights(msg_ids)

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

    def _should_call_ai(self, events: list[AnomalyEvent]) -> bool:
        severity_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = severity_rank.get(self.settings.ai_min_severity.lower(), 3)
        return any(severity_rank.get(event.severity.lower(), 0) >= threshold for event in events)

    @staticmethod
    def _dedupe_events(events: list[AnomalyEvent]) -> list[AnomalyEvent]:
        deduped: dict[tuple[str, str, str, float, float], AnomalyEvent] = {}
        for event in events:
            key = (
                event.pod_name,
                event.namespace,
                event.metric_type.value,
                round(event.metric_value, 3),
                round(event.z_score, 3),
            )
            deduped[key] = event
        return sorted(deduped.values(), key=lambda event: event.timestamp)[-10:]

    @staticmethod
    def _dedupe_metrics(metrics: list[MetricPoint]) -> list[MetricPoint]:
        deduped: dict[tuple[str, str, str, float], MetricPoint] = {}
        for metric in metrics:
            key = (metric.pod_name, metric.namespace, metric.metric_type.value, round(metric.metric_value, 3))
            deduped[key] = metric
        return sorted(deduped.values(), key=lambda metric: metric.timestamp)[-50:]
