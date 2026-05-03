from fastapi import APIRouter, Depends, Request

from app.core.auth import require_api_key


router = APIRouter()


# ── Public endpoints (no auth) ─────────────────────────────────────────

@router.get("/health")
async def health(request: Request) -> dict:
    app_state = request.app.state.runtime
    storage = request.app.state.storage
    queue = request.app.state.queue
    gemini = request.app.state.gemini
    app_state.database_ok = await storage.health()
    app_state.queue_ok = await queue.health()
    return {
        "database": "ok" if app_state.database_ok else "unavailable",
        "queue": "ok" if app_state.queue_ok else "unavailable",
        "collector": "ok" if app_state.collector_ok else "degraded",
        "gemini": gemini.status(),
        "collector_error": app_state.collector_last_error,
        "gemini_error": app_state.gemini_last_error,
    }


@router.get("/system/metrics")
async def system_metrics(request: Request) -> dict:
    runtime = request.app.state.runtime
    queue = request.app.state.queue
    try:
        queue_size = await queue.size()
    except Exception:
        queue_size = -1
        runtime.error_count += 1
    return {
        "queue_size": queue_size,
        "processing_latency_ms": runtime.avg_processing_latency_ms,
        "error_rate_per_second": runtime.error_rate,
        "gemini_latency_ms": runtime.avg_gemini_latency_ms,
        "processed_count": runtime.processed_count,
        "error_count": runtime.error_count,
    }


# ── Protected endpoints (require API key when configured) ──────────────

@router.get("/gemini/test", dependencies=[Depends(require_api_key)])
async def gemini_test(request: Request) -> dict:
    return await request.app.state.gemini.test()


@router.get("/metrics/recent", dependencies=[Depends(require_api_key)])
async def recent_metrics(request: Request) -> list[dict]:
    storage = request.app.state.storage
    rows = await storage.recent_metrics(100)
    if rows:
        return rows
    return [metric.model_dump(mode="json") for metric in request.app.state.runtime.recent_metrics]


@router.get("/anomalies", dependencies=[Depends(require_api_key)])
async def anomalies(request: Request) -> list[dict]:
    storage = request.app.state.storage
    rows = await storage.anomalies()
    if rows:
        return rows
    return [event.model_dump(mode="json") for event in request.app.state.runtime.anomalies]


@router.get("/dependencies", dependencies=[Depends(require_api_key)])
async def dependencies(request: Request) -> dict:
    return request.app.state.correlation.export_json()


@router.get("/insights", dependencies=[Depends(require_api_key)])
async def insights(request: Request) -> list[dict]:
    storage = request.app.state.storage
    rows = await storage.insights()
    if rows:
        return rows
    return [insight.model_dump(mode="json") for insight in request.app.state.runtime.insights]
