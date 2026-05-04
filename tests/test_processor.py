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


def test_gemini_reuses_cached_insight_for_repeated_signature():
    """Repeated anomaly signatures should not trigger another Gemini request."""
    from app.services.gemini import GeminiReasoningEngine
    import asyncio

    settings = Settings(gemini_api_key="fake-key", gemini_requests_per_minute=10000)
    state = RuntimeState()
    engine = GeminiReasoningEngine(settings, state)
    engine._generate = MagicMock(  # noqa: SLF001
        return_value={
            "root_cause": "CPU saturation",
            "confidence": "High",
            "recommendation": "Increase CPU limits for the pod.",
        }
    )

    events = [
        AnomalyEvent(
            pod_name="api",
            namespace="default",
            metric_type=MetricType.cpu,
            metric_value=99.0,
            severity="critical",
            z_score=8.5,
        )
    ]
    metrics = [MetricPoint(pod_name="api", namespace="default", metric_type=MetricType.cpu, metric_value=99.0)]

    first = asyncio.run(engine.analyze("api", "default", events, metrics))
    second = asyncio.run(engine.analyze("api", "default", events, metrics))

    assert first.fallback is False
    assert second.root_cause == "CPU saturation"
    assert engine._generate.call_count == 1  # noqa: SLF001


def test_insight_worker_batches_by_pod_and_skips_ai_for_noncritical():
    """Medium/high anomalies should use deterministic insights instead of Gemini."""
    from app.services.processor import InsightWorker
    import asyncio

    settings = Settings(ai_min_severity="critical")
    queue = MagicMock()
    queue.ack_insights = AsyncMock()
    storage = MagicMock()
    storage.insert_insight = AsyncMock()
    gemini = MagicMock()
    gemini.analyze = AsyncMock()
    gemini.rules_insight = MagicMock(
        return_value=Insight(
            pod="api",
            namespace="default",
            root_cause="rules",
            confidence="Low",
            recommendation="review",
            fallback=True,
        )
    )
    state = RuntimeState()
    worker = InsightWorker(settings, queue, storage, gemini, state)

    event = AnomalyEvent(
        pod_name="api",
        namespace="default",
        metric_type=MetricType.cpu,
        metric_value=90.0,
        severity="high",
        z_score=4.0,
    )
    metric = MetricPoint(pod_name="api", namespace="default", metric_type=MetricType.cpu, metric_value=90.0)
    request = {
        "pod": "api",
        "namespace": "default",
        "events": [event.model_dump(mode="json")],
        "metrics": [metric.model_dump(mode="json")],
    }

    asyncio.run(worker._process_batch([("1-0", request), ("2-0", request)]))  # noqa: SLF001

    gemini.analyze.assert_not_awaited()
    gemini.rules_insight.assert_called_once()
    queue.ack_insights.assert_awaited_once_with(["1-0", "2-0"])


def test_insight_worker_calls_ai_for_critical_batch():
    """Critical batches should still go to Gemini after batching."""
    from app.services.processor import InsightWorker
    import asyncio

    settings = Settings(ai_min_severity="critical")
    queue = MagicMock()
    queue.ack_insights = AsyncMock()
    storage = MagicMock()
    storage.insert_insight = AsyncMock()
    gemini = MagicMock()
    gemini.analyze = AsyncMock(
        return_value=Insight(
            pod="api",
            namespace="default",
            root_cause="critical",
            confidence="High",
            recommendation="scale",
            fallback=False,
        )
    )
    gemini.rules_insight = MagicMock()
    state = RuntimeState()
    worker = InsightWorker(settings, queue, storage, gemini, state)

    event = AnomalyEvent(
        pod_name="api",
        namespace="default",
        metric_type=MetricType.memory,
        metric_value=99.0,
        severity="critical",
        z_score=9.0,
    )
    request = {
        "pod": "api",
        "namespace": "default",
        "events": [event.model_dump(mode="json")],
        "metrics": [],
    }

    asyncio.run(worker._process_batch([("1-0", request)]))  # noqa: SLF001

    gemini.analyze.assert_awaited_once()
    gemini.rules_insight.assert_not_called()
    queue.ack_insights.assert_awaited_once_with(["1-0"])
