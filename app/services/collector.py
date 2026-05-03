import asyncio

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.state import RuntimeState
from app.services.prometheus import PrometheusClient
from app.services.queue import MetricQueue
from app.services.storage import StorageEngine


class MetricsCollector:
    def __init__(self, settings: Settings, queue: MetricQueue, storage: StorageEngine, state: RuntimeState) -> None:
        self.settings = settings
        self.queue = queue
        self.storage = storage
        self.state = state
        self.prometheus = PrometheusClient(settings)
        self.logger = get_logger(__name__, "collector")

    async def run(self) -> None:
        backoff = 1
        while True:
            try:
                metrics = await self.prometheus.collect_all()
                await self.queue.publish_many(metrics)
                await self.storage.insert_metrics(metrics)
                self.state.collector_ok = True
                self.state.collector_last_error = None
                for metric in metrics:
                    self.state.recent_metrics.appendleft(metric)
                self.logger.info(
                    "metric_collected",
                    extra={"event": "metric_collected", "status": "success", "count": len(metrics)},
                )
                backoff = 1
                await asyncio.sleep(self.settings.metrics_poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.collector_ok = False
                self.state.collector_last_error = str(exc)
                self.state.error_count += 1
                self.logger.error(
                    "collector_failed",
                    extra={"event": "collector_failed", "status": "error", "error": str(exc)},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def close(self) -> None:
        await self.prometheus.close()
