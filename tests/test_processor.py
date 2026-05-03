"""Tests for the event processor and insight worker."""
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque

import pytest

from app.core.config import Settings
from app.core.models import AnomalyEvent, Insight, MetricPoint, MetricType
from app.core.state import RuntimeState


def test_gemini_fallback_returns_insight():
    """GeminiReasoningEngine should return a fallback insight when no API key is set."""
    from app.services.gemini import GeminiReasoningEngine
    import asyncio

    settings = Settings(gemini_api_key=None)
    state = RuntimeState()
    engine = GeminiReasoningEngine(settings, state)

    events = [
        AnomalyEvent(
            pod_name="api", namespace="default",
            metric_type=MetricType.cpu, metric_value=99.0,
            severity="high", z_score=3.5,
        )
    ]
    metrics = [
        MetricPoint(pod_name="api", namespace="default", metric_type=MetricType.cpu, metric_value=99.0)
    ]

    insight = asyncio.run(engine.analyze("api", "default", events, metrics))
    assert insight.fallback is True
    assert insight.pod == "api"
    assert "cpu" in insight.root_cause.lower()


def test_gemini_status_not_configured():
    """Status should be 'not_configured' when no API key."""
    from app.services.gemini import GeminiReasoningEngine

    settings = Settings(gemini_api_key=None)
    state = RuntimeState()
    engine = GeminiReasoningEngine(settings, state)
    assert engine.status() == "not_configured"


def test_gemini_configured_flag():
    """configured property should reflect API key presence."""
    from app.services.gemini import GeminiReasoningEngine

    settings_no_key = Settings(gemini_api_key=None)
    state = RuntimeState()
    engine = GeminiReasoningEngine(settings_no_key, state)
    assert engine.configured is False

    settings_with_key = Settings(gemini_api_key="fake-key")
    engine2 = GeminiReasoningEngine(settings_with_key, state)
    assert engine2.configured is True


def test_gemini_uses_configurable_timeout():
    """Timeout should come from settings."""
    from app.services.gemini import GeminiReasoningEngine

    settings = Settings(gemini_api_key=None, gemini_timeout_seconds=20.0)
    state = RuntimeState()
    engine = GeminiReasoningEngine(settings, state)
    assert engine.settings.gemini_timeout_seconds == 20.0
