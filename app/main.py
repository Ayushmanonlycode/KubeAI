import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.state import RuntimeState
from app.services.collector import MetricsCollector
from app.services.correlation import CorrelationEngine
from app.services.gemini import GeminiReasoningEngine
from app.services.processor import EventProcessor
from app.services.queue import MetricQueue
from app.services.storage import StorageEngine


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    runtime = RuntimeState()
    queue = MetricQueue(settings)
    storage = StorageEngine(settings)
    correlation = CorrelationEngine(settings)
    gemini = GeminiReasoningEngine(settings, runtime)
    collector = MetricsCollector(settings, queue, storage, runtime)
    processor = EventProcessor(settings, queue, storage, correlation, gemini, runtime)

    app.state.settings = settings
    app.state.runtime = runtime
    app.state.queue = queue
    app.state.storage = storage
    app.state.correlation = correlation
    app.state.gemini = gemini

    await storage.start()
    tasks = [
        asyncio.create_task(collector.run(), name="metrics-collector"),
        asyncio.create_task(processor.run(), name="event-processor"),
        asyncio.create_task(storage.retention_loop(), name="metrics-retention"),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await collector.close()
        await queue.close()
        await storage.close()


app = FastAPI(title="AI Kubernetes Observability Backend", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

