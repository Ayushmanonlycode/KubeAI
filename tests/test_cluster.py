"""Tests for the cluster-wide SRE incident intelligence endpoint."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.models import (
    ClusterIncidentReport,
    CriticalIncident,
    MetricType,
)
from app.core.state import RuntimeState
from app.services.cluster import ClusterAnalyzer


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_analyzer(gemini_key: str | None = None) -> ClusterAnalyzer:
    """Build a ClusterAnalyzer with mocked storage, correlation, and gemini."""
    settings = Settings(gemini_api_key=gemini_key)
    storage = MagicMock()
    correlation = MagicMock()
    state = RuntimeState()
    from app.services.gemini import GeminiReasoningEngine
    gemini = GeminiReasoningEngine(settings, state)
    return ClusterAnalyzer(settings, storage, correlation, gemini, state)


def _mock_anomaly(pod: str, ns: str, mtype: str, value: float, severity: str, z_score: float) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc),
        "pod_name": pod,
        "namespace": ns,
        "metric_type": mtype,
        "metric_value": value,
        "severity": severity,
        "z_score": z_score,
    }


def _mock_metric(pod: str, ns: str, mtype: str, value: float) -> dict:
    return {
        "pod_name": pod,
        "namespace": ns,
        "metric_type": mtype,
        "metric_value": value,
        "timestamp": datetime.now(timezone.utc),
    }


# ── Tests ────────────────────────────────────────────────────────────────

def test_fallback_when_gemini_not_configured():
    """With no API key, analyze() should return a fallback report."""
    analyzer = _make_analyzer(gemini_key=None)
    analyzer.storage.anomalies_since = AsyncMock(return_value=[
        _mock_anomaly("api", "default", "cpu", 95.0, "critical", 8.5),
        _mock_anomaly("db", "default", "memory", 88.0, "high", 4.2),
    ])
    analyzer.storage.metrics_since = AsyncMock(return_value=[
        _mock_metric("api", "default", "cpu", 95.0),
    ])
    analyzer.correlation.export_json = MagicMock(return_value={"edges": []})

    report = asyncio.run(analyzer.analyze())
    assert isinstance(report, ClusterIncidentReport)
    assert report.fallback is True
    assert len(report.critical_incidents) >= 1
    assert report.critical_incidents[0].confidence == "Low"
    assert "api" in report.critical_incidents[0].affected_pods[0]


def test_fallback_empty_anomalies():
    """With no anomalies, fallback should report healthy cluster."""
    analyzer = _make_analyzer(gemini_key=None)
    analyzer.storage.anomalies_since = AsyncMock(return_value=[])
    analyzer.storage.metrics_since = AsyncMock(return_value=[])
    analyzer.correlation.export_json = MagicMock(return_value={"edges": []})

    report = asyncio.run(analyzer.analyze())
    assert report.fallback is True
    assert len(report.critical_incidents) == 1
    assert "no active anomalies" in report.critical_incidents[0].incident_title.lower()
    assert report.critical_incidents[0].confidence == "High"


def test_fallback_ranks_critical_above_medium():
    """Deterministic fallback should rank critical severity above medium."""
    analyzer = _make_analyzer(gemini_key=None)
    analyzer.storage.anomalies_since = AsyncMock(return_value=[
        _mock_anomaly("low-pod", "test", "cpu", 55.0, "medium", 2.2),
        _mock_anomaly("hot-pod", "production", "memory", 99.0, "critical", 9.1),
    ])
    analyzer.storage.metrics_since = AsyncMock(return_value=[])
    analyzer.correlation.export_json = MagicMock(return_value={"edges": []})

    report = asyncio.run(analyzer.analyze())
    assert report.critical_incidents[0].rank == 1
    assert "hot-pod" in report.critical_incidents[0].affected_pods[0]


def test_fallback_max_five_incidents():
    """Fallback should return at most 5 incidents even with many anomalies."""
    analyzer = _make_analyzer(gemini_key=None)
    anomalies = [
        _mock_anomaly(f"pod-{i}", "default", "cpu", 80.0 + i, "high", 3.0 + i)
        for i in range(10)
    ]
    analyzer.storage.anomalies_since = AsyncMock(return_value=anomalies)
    analyzer.storage.metrics_since = AsyncMock(return_value=[])
    analyzer.correlation.export_json = MagicMock(return_value={"edges": []})

    report = asyncio.run(analyzer.analyze())
    assert len(report.critical_incidents) <= 5


def test_cache_returns_same_report():
    """Second call within TTL should return the cached report without re-querying."""
    analyzer = _make_analyzer(gemini_key=None)
    analyzer.storage.anomalies_since = AsyncMock(return_value=[
        _mock_anomaly("api", "default", "cpu", 95.0, "critical", 8.0),
    ])
    analyzer.storage.metrics_since = AsyncMock(return_value=[])
    analyzer.correlation.export_json = MagicMock(return_value={"edges": []})

    first = asyncio.run(analyzer.analyze())
    second = asyncio.run(analyzer.analyze())

    assert first is second
    # Storage should only be queried once (cached on second call)
    assert analyzer.storage.anomalies_since.await_count == 1


def test_snapshot_includes_dependency_edges():
    """Snapshot should include dependency edges from the correlation engine."""
    analyzer = _make_analyzer(gemini_key=None)
    analyzer.storage.anomalies_since = AsyncMock(return_value=[
        _mock_anomaly("api", "default", "cpu", 95.0, "critical", 8.0),
        _mock_anomaly("db", "default", "memory", 90.0, "high", 5.0),
    ])
    analyzer.storage.metrics_since = AsyncMock(return_value=[
        _mock_metric("api", "default", "cpu", 95.0),
        _mock_metric("db", "default", "memory", 90.0),
    ])
    analyzer.correlation.export_json = MagicMock(return_value={
        "edges": [
            {"source": "default/api", "target": "default/db", "coefficient": 0.95, "reason": "correlated"},
        ]
    })

    snapshot = asyncio.run(analyzer._build_snapshot())  # noqa: SLF001
    assert len(snapshot["dependency_edges"]) == 1
    assert snapshot["dependency_edges"][0]["source"] == "default/api"
    assert snapshot["total_anomalies"] == 2


def test_report_model_validation():
    """ClusterIncidentReport and CriticalIncident should validate correctly."""
    incident = CriticalIncident(
        rank=1,
        incident_title="CPU saturation on payment-service",
        affected_services=["payment-service"],
        affected_pods=["default/payment-service-abc123"],
        root_cause="Unbounded thread pool causing CPU exhaustion",
        impact="Latency SLO breach for payment processing",
        confidence="High",
        recommendation="Set CPU limits to 500m and add HPA with 70% target",
    )
    report = ClusterIncidentReport(
        cluster_summary="Single critical incident affecting payments",
        critical_incidents=[incident],
        fallback=False,
    )
    data = report.model_dump(mode="json")
    assert data["cluster_summary"] == "Single critical incident affecting payments"
    assert data["critical_incidents"][0]["rank"] == 1
    assert data["critical_incidents"][0]["confidence"] == "High"
    assert data["fallback"] is False


def test_gemini_success_returns_non_fallback():
    """When Gemini returns valid JSON, the report should not be a fallback."""
    analyzer = _make_analyzer(gemini_key="fake-key")
    analyzer.storage.anomalies_since = AsyncMock(return_value=[
        _mock_anomaly("api", "default", "cpu", 95.0, "critical", 8.0),
    ])
    analyzer.storage.metrics_since = AsyncMock(return_value=[])
    analyzer.correlation.export_json = MagicMock(return_value={"edges": []})

    mock_response = {
        "cluster_summary": "Critical CPU incident on api pod",
        "critical_incidents": [
            {
                "rank": 1,
                "incident_title": "CPU saturation on default/api",
                "affected_services": ["api"],
                "affected_pods": ["default/api"],
                "root_cause": "Unbounded request processing",
                "impact": "Latency breach",
                "confidence": "High",
                "recommendation": "Scale horizontally",
            }
        ],
    }

    # Mock the _generate method to return our response
    analyzer._generate = MagicMock(return_value=mock_response)  # noqa: SLF001
    # Mock throttle to be a no-op
    analyzer.gemini._throttle = AsyncMock()  # noqa: SLF001

    report = asyncio.run(analyzer.analyze())
    assert report.fallback is False
    assert report.cluster_summary == "Critical CPU incident on api pod"
    assert len(report.critical_incidents) == 1
    assert report.critical_incidents[0].confidence == "High"


def test_gemini_failure_falls_back():
    """When Gemini fails all retries, the report should be a fallback."""
    analyzer = _make_analyzer(gemini_key="fake-key")
    analyzer.storage.anomalies_since = AsyncMock(return_value=[
        _mock_anomaly("api", "default", "cpu", 95.0, "critical", 8.0),
    ])
    analyzer.storage.metrics_since = AsyncMock(return_value=[])
    analyzer.correlation.export_json = MagicMock(return_value={"edges": []})

    # Mock _generate to always raise
    analyzer._generate = MagicMock(side_effect=RuntimeError("Gemini down"))  # noqa: SLF001
    analyzer.gemini._throttle = AsyncMock()  # noqa: SLF001

    report = asyncio.run(analyzer.analyze())
    assert report.fallback is True
    assert "Gemini" in report.critical_incidents[0].root_cause or "unavailable" in report.cluster_summary.lower()
