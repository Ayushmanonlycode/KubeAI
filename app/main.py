import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.state import RuntimeState
from app.services.correlation import CorrelationEngine
from app.services.gemini import GeminiReasoningEngine
from app.services.queue import MetricQueue
from app.services.storage import StorageEngine


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = get_logger(__name__, "api")
    settings = get_settings()
    runtime = RuntimeState()
    queue = MetricQueue(settings)
    storage = StorageEngine(settings)
    correlation = CorrelationEngine(settings)
    gemini = GeminiReasoningEngine(settings, runtime)

    app.state.settings = settings
    app.state.runtime = runtime
    app.state.queue = queue
    app.state.storage = storage
    app.state.correlation = correlation
    app.state.gemini = gemini

    await storage.start()

    # Validate production configuration
    if settings.environment == "production" and not settings.api_key:
        raise RuntimeError("API_KEY must be set when ENVIRONMENT=production")

    logger.info("api_started", extra={"event": "api_started", "status": "success"})
    try:
        yield
    finally:
        await queue.close()
        await storage.close()


app = FastAPI(title="AI Kubernetes Observability Backend", version="1.0.0", lifespan=lifespan)

settings = get_settings()

# Only add CORS middleware if origins are configured
if settings.cors_origins:
    logger_init = get_logger(__name__, "api")
    if ["*"] == settings.cors_origins and True:  # allow_credentials is always True
        logger_init.warning(
            "cors_wildcard_with_credentials",
            extra={
                "event": "cors_wildcard_with_credentials",
                "status": "warning",
                "detail": "CORS allows all origins with credentials — restrict in production",
            },
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(router)
