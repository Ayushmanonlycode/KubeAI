"""Standalone background worker — runs collector, processor, and retention.

Usage:
    python -m app.worker

This process does NOT serve HTTP. It connects to the same Redis, Postgres,
and Prometheus instances as the API, but runs background loops:

- MetricsCollector: polls Prometheus on an interval
- EventProcessor: consumes metrics from the Redis stream, runs detection
- InsightWorker: consumes insight requests, calls Gemini with concurrency limits
- StorageEngine.retention_loop: hourly data cleanup
"""
import asyncio
import signal
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.state import RuntimeState
from app.services.collector import MetricsCollector
from app.services.correlation import CorrelationEngine
from app.services.gemini import GeminiReasoningEngine
from app.services.processor import EventProcessor, InsightWorker
from app.services.queue import MetricQueue
from app.services.storage import StorageEngine


async def main() -> None:
    configure_logging()
    logger = get_logger(__name__, "worker")
    settings = get_settings()
    runtime = RuntimeState()

    queue = MetricQueue(settings)
    storage = StorageEngine(settings)
    correlation = CorrelationEngine(settings)
    gemini = GeminiReasoningEngine(settings, runtime)
    collector = MetricsCollector(settings, queue, storage, runtime)
    processor = EventProcessor(settings, queue, storage, correlation, runtime)
    insight_worker = InsightWorker(settings, queue, storage, gemini, runtime)

    await storage.start()
    await queue.ensure_group()

    logger.info("worker_started", extra={"event": "worker_started", "status": "success"})

    tasks = [
        asyncio.create_task(collector.run(), name="metrics-collector"),
        asyncio.create_task(processor.run(), name="event-processor"),
        asyncio.create_task(insight_worker.run(), name="insight-worker"),
        asyncio.create_task(storage.retention_loop(), name="metrics-retention"),
    ]

    stop = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("worker_shutdown", extra={"event": "worker_shutdown", "status": "stopping"})
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    await stop.wait()

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await collector.close()
    await queue.close()
    await storage.close()
    logger.info("worker_stopped", extra={"event": "worker_stopped", "status": "success"})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
