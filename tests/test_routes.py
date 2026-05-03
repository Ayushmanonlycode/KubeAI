"""Tests for API routes — liveness, readiness, health."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.state import RuntimeState


def _create_test_app():
    """Create a test app with mocked dependencies."""
    from fastapi import FastAPI
    from app.api.routes import router
    from app.services.gemini import GeminiReasoningEngine

    app = FastAPI()
    app.include_router(router)

    settings = Settings(gemini_api_key=None)
    runtime = RuntimeState()
    gemini = GeminiReasoningEngine(settings, runtime)

    # Mock storage and queue
    mock_storage = MagicMock()
    mock_storage.health = AsyncMock(return_value=True)
    mock_storage.recent_metrics = AsyncMock(return_value=[])
    mock_storage.anomalies = AsyncMock(return_value=[])
    mock_storage.insights = AsyncMock(return_value=[])

    mock_queue = MagicMock()
    mock_queue.health = AsyncMock(return_value=True)
    mock_queue.size = AsyncMock(return_value=0)

    mock_correlation = MagicMock()
    mock_correlation.export_json = MagicMock(return_value={"nodes": [], "edges": []})

    app.state.settings = settings
    app.state.runtime = runtime
    app.state.storage = mock_storage
    app.state.queue = mock_queue
    app.state.correlation = mock_correlation
    app.state.gemini = gemini

    return app


def test_liveness_endpoint():
    """GET /live should always return ok."""
    app = _create_test_app()
    client = TestClient(app)
    response = client.get("/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_endpoint_healthy():
    """GET /ready should return ready when DB and queue are ok."""
    app = _create_test_app()
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "ok"
    assert data["queue"] == "ok"


def test_readiness_endpoint_unhealthy():
    """GET /ready should return not_ready when DB is down."""
    app = _create_test_app()
    app.state.storage.health = AsyncMock(return_value=False)
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["database"] == "unavailable"


def test_health_does_not_expose_errors():
    """GET /health should not contain collector_error or gemini_error."""
    app = _create_test_app()
    app.state.runtime.collector_last_error = "some internal error"
    app.state.runtime.gemini_last_error = "some gemini error"
    client = TestClient(app)
    response = client.get("/health")
    data = response.json()
    assert "collector_error" not in data
    assert "gemini_error" not in data


def test_protected_endpoint_requires_auth():
    """Protected endpoints should require API key when configured."""
    app = _create_test_app()
    app.state.settings = Settings(api_key="secret-key", gemini_api_key=None)
    client = TestClient(app)

    response = client.get("/metrics/recent")
    assert response.status_code == 401

    response = client.get("/metrics/recent", headers={"X-API-Key": "secret-key"})
    assert response.status_code == 200


def test_protected_endpoint_open_without_api_key():
    """Protected endpoints should be open when no API key is configured."""
    app = _create_test_app()
    client = TestClient(app)
    response = client.get("/metrics/recent")
    assert response.status_code == 200
