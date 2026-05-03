import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.models import AnomalyEvent, Insight, MetricPoint


class StorageEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool: asyncpg.Pool | None = None
        self.logger = get_logger(__name__, "storage")

    async def start(self) -> None:
        """Connect to the database. Raises RuntimeError if all retries fail."""
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                self.pool = await asyncpg.create_pool(self.settings.database_url, min_size=1, max_size=10)
                await self._migrate()
                self.logger.info("database_connected", extra={"event": "database_connected", "status": "success"})
                return
            except Exception as exc:
                last_error = exc
                self.logger.warning(
                    "database_connect_failed",
                    extra={"event": "database_connect_failed", "status": "retry", "attempt": attempt + 1, "error": str(exc)},
                )
                await asyncio.sleep(min(2**attempt, 30))
        raise RuntimeError(f"Database unavailable after 5 retries: {last_error}")

    async def _migrate(self) -> None:
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    id BIGSERIAL PRIMARY KEY,
                    pod_name TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    metric_value DOUBLE PRECISION NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);
                CREATE INDEX IF NOT EXISTS idx_metrics_pod_name ON metrics(pod_name);
                CREATE INDEX IF NOT EXISTS idx_metrics_metric_type ON metrics(metric_type);
                CREATE INDEX IF NOT EXISTS idx_metrics_composite ON metrics(namespace, pod_name, metric_type, timestamp);

                CREATE TABLE IF NOT EXISTS anomalies (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    pod_name TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    metric_value DOUBLE PRECISION NOT NULL,
                    severity TEXT NOT NULL,
                    z_score DOUBLE PRECISION NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_anomalies_timestamp ON anomalies(timestamp);
                CREATE INDEX IF NOT EXISTS idx_anomalies_pod ON anomalies(namespace, pod_name, timestamp);

                CREATE TABLE IF NOT EXISTS insights (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    pod_name TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    fallback BOOLEAN NOT NULL,
                    source_events JSONB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_insights_timestamp ON insights(timestamp);
                """
            )

    async def health(self) -> bool:
        if self.pool is None:
            return False
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as exc:
            self.logger.error("database_health_failed", extra={"event": "database_health_failed", "status": "error", "error": str(exc)})
            return False

    async def insert_metrics(self, metrics: list[MetricPoint]) -> None:
        if self.pool is None or not metrics:
            return
        rows = [(m.pod_name, m.namespace, m.metric_type.value, m.metric_value, m.timestamp) for m in metrics]
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO metrics (pod_name, namespace, metric_type, metric_value, timestamp)
                VALUES ($1, $2, $3, $4, $5)
                """,
                rows,
            )

    async def insert_anomaly(self, anomaly: AnomalyEvent) -> None:
        """Insert a single anomaly. Prefer insert_anomalies for batches."""
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO anomalies (timestamp, pod_name, namespace, metric_type, metric_value, severity, z_score)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                anomaly.timestamp,
                anomaly.pod_name,
                anomaly.namespace,
                anomaly.metric_type.value,
                anomaly.metric_value,
                anomaly.severity,
                anomaly.z_score,
            )

    async def insert_anomalies(self, anomalies: list[AnomalyEvent]) -> None:
        """Batch insert anomalies using executemany."""
        if self.pool is None or not anomalies:
            return
        rows = [
            (a.timestamp, a.pod_name, a.namespace, a.metric_type.value, a.metric_value, a.severity, a.z_score)
            for a in anomalies
        ]
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO anomalies (timestamp, pod_name, namespace, metric_type, metric_value, severity, z_score)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                rows,
            )

    async def insert_insight(self, insight: Insight) -> None:
        """Insert a single insight. Prefer insert_insights for batches."""
        if self.pool is None:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO insights (timestamp, pod_name, namespace, root_cause, confidence, recommendation, fallback, source_events)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                """,
                insight.timestamp,
                insight.pod,
                insight.namespace,
                insight.root_cause,
                insight.confidence,
                insight.recommendation,
                insight.fallback,
                json.dumps(insight.source_events),
            )

    async def insert_insights(self, insights: list[Insight]) -> None:
        """Batch insert insights using executemany."""
        if self.pool is None or not insights:
            return
        rows = [
            (i.timestamp, i.pod, i.namespace, i.root_cause, i.confidence, i.recommendation, i.fallback, json.dumps(i.source_events))
            for i in insights
        ]
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO insights (timestamp, pod_name, namespace, root_cause, confidence, recommendation, fallback, source_events)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                """,
                rows,
            )

    async def recent_metrics(self, limit: int = 100) -> list[dict[str, Any]]:
        if self.pool is None:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pod_name, namespace, metric_type, metric_value, timestamp
                FROM metrics
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(row) for row in rows]

    async def anomalies(self, limit: int = 200) -> list[dict[str, Any]]:
        if self.pool is None:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT timestamp, pod_name, namespace, metric_type, metric_value, severity, z_score
                FROM anomalies
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(row) for row in rows]

    async def insights(self, limit: int = 50) -> list[dict[str, Any]]:
        if self.pool is None:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT timestamp, pod_name AS pod, namespace, root_cause, confidence, recommendation, fallback, source_events
                FROM insights
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(row) for row in rows]

    async def retention_loop(self) -> None:
        while True:
            try:
                await self.delete_old_data()
            except Exception as exc:
                self.logger.error("retention_failed", extra={"event": "retention_failed", "status": "error", "error": str(exc)})
            await asyncio.sleep(3600)

    async def delete_old_data(self) -> None:
        """Delete rows older than ``retention_days`` from metrics, anomalies, and insights."""
        if self.pool is None:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.settings.retention_days)
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM metrics WHERE timestamp < $1", cutoff)
            await conn.execute("DELETE FROM anomalies WHERE timestamp < $1", cutoff)
            await conn.execute("DELETE FROM insights WHERE timestamp < $1", cutoff)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
