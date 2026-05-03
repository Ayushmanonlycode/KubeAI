"""Tests for Prometheus client concurrent queries."""
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.core.config import Settings
from app.core.models import MetricType
from app.services.prometheus import PrometheusClient


def test_prometheus_uses_configurable_timeout():
    """Timeout should come from settings, not hardcoded."""
    settings = Settings(prometheus_timeout_seconds=15.0)
    client = PrometheusClient(settings)
    assert client.client.timeout.read == 15.0
    asyncio.run(client.close())


def test_prometheus_collects_all_metric_types():
    """collect_all should query all defined PROMQL metrics."""
    from app.services.prometheus import PROMQL
    expected_types = {MetricType.cpu, MetricType.memory, MetricType.disk_io, MetricType.network, MetricType.pvc_usage, MetricType.restarts}
    assert set(PROMQL.keys()) == expected_types


@pytest.mark.asyncio
async def test_collect_all_handles_partial_failures():
    """One failed query should not prevent others from returning results."""
    settings = Settings()
    client = PrometheusClient(settings)

    call_count = 0

    async def mock_query(metric_type):
        nonlocal call_count
        call_count += 1
        if metric_type == MetricType.cpu:
            raise ConnectionError("timeout")
        return []

    with patch.object(client, "query_metric", side_effect=mock_query):
        results = await client.collect_all()

    # Should have called all metric types
    assert call_count == len(MetricType) - 1  # logs is not in PROMQL
    # Should not raise despite CPU failure
    assert isinstance(results, list)

    await client.close()
