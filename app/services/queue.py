"""Reliable metric queue backed by Redis Streams.

Uses XADD for publishing, XREADGROUP + XACK for at-least-once delivery.
Messages stay pending until explicitly acknowledged after successful processing.
"""
import asyncio
import json
import socket
from typing import Any, Iterable

import redis.asyncio as redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.models import AnomalyEvent, MetricPoint


class MetricQueue:
    STREAM = "metrics"
    INSIGHT_STREAM = "insight-requests"
    GROUP = "workers"
    DLQ_STREAM = "metrics-dlq"
    MAX_RETRIES = 5

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = redis.from_url(settings.redis_url, decode_responses=True)
        self.consumer = socket.gethostname()
        self.logger = get_logger(__name__, "queue")

    # ── Setup ──────────────────────────────────────────────────────────

    async def ensure_group(self) -> None:
        """Create consumer groups idempotently for both streams.

        Retries up to 5 times with exponential backoff to survive
        transient DNS/connection failures during container startup.
        """
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                for stream in (self.STREAM, self.INSIGHT_STREAM):
                    try:
                        await self.client.xgroup_create(stream, self.GROUP, id="0", mkstream=True)
                    except redis.ResponseError as exc:
                        if "BUSYGROUP" not in str(exc):
                            raise
                self.logger.info("redis_connected", extra={"event": "redis_connected", "status": "success"})
                return
            except Exception as exc:
                last_error = exc
                self.logger.warning(
                    "redis_connect_failed",
                    extra={"event": "redis_connect_failed", "status": "retry", "attempt": attempt + 1, "error": str(exc)},
                )
                await asyncio.sleep(min(2 ** attempt, 30))
        raise RuntimeError(f"Redis unavailable after 5 retries: {last_error}")

    # ── Publishing ─────────────────────────────────────────────────────

    async def publish_many(self, metrics: Iterable[MetricPoint]) -> None:
        pipe = self.client.pipeline()
        count = 0
        for metric in metrics:
            pipe.xadd(self.STREAM, {"data": metric.model_dump_json()})
            count += 1
        if count:
            await pipe.execute()

    async def publish_insight_request(
        self,
        pod: str,
        namespace: str,
        events: list[AnomalyEvent],
        metrics: list[MetricPoint],
    ) -> None:
        request = {
            "pod": pod,
            "namespace": namespace,
            "events": [e.model_dump(mode="json") for e in events],
            "metrics": [m.model_dump(mode="json") for m in metrics],
        }
        await self.client.xadd(self.INSIGHT_STREAM, {"data": json.dumps(request, default=str)})

    # ── Consuming ──────────────────────────────────────────────────────

    async def consume(self, timeout_ms: int = 2000) -> tuple[str, MetricPoint] | None:
        """Read next unacknowledged metric from the stream."""
        results = await self.client.xreadgroup(
            self.GROUP, self.consumer,
            {self.STREAM: ">"},
            count=1,
            block=timeout_ms,
        )
        if not results:
            return None
        stream_name, messages = results[0]
        msg_id, fields = messages[0]
        metric = MetricPoint.model_validate_json(fields["data"])
        return msg_id, metric

    async def consume_insight_request(self, timeout_ms: int = 2000) -> tuple[str, dict] | None:
        """Read next insight request from the insight stream."""
        results = await self.client.xreadgroup(
            self.GROUP, self.consumer,
            {self.INSIGHT_STREAM: ">"},
            count=1,
            block=timeout_ms,
        )
        if not results:
            return None
        stream_name, messages = results[0]
        msg_id, fields = messages[0]
        request = json.loads(fields["data"])
        return msg_id, request

    async def consume_insight_requests(self, count: int, timeout_ms: int = 2000) -> list[tuple[str, dict]]:
        """Read a small batch of insight requests from the insight stream."""
        results = await self.client.xreadgroup(
            self.GROUP, self.consumer,
            {self.INSIGHT_STREAM: ">"},
            count=count,
            block=timeout_ms,
        )
        if not results:
            return []
        _, messages = results[0]
        return [(msg_id, json.loads(fields["data"])) for msg_id, fields in messages]

    # ── Acknowledgement ────────────────────────────────────────────────

    async def ack(self, msg_id: str) -> None:
        await self.client.xack(self.STREAM, self.GROUP, msg_id)

    async def ack_insight(self, msg_id: str) -> None:
        await self.client.xack(self.INSIGHT_STREAM, self.GROUP, msg_id)

    async def ack_insights(self, msg_ids: list[str]) -> None:
        if msg_ids:
            await self.client.xack(self.INSIGHT_STREAM, self.GROUP, *msg_ids)

    # ── Dead letter ────────────────────────────────────────────────────

    async def requeue_stale(self, idle_ms: int = 60_000) -> int:
        """Claim messages idle for too long. Move to DLQ after MAX_RETRIES."""
        moved = 0
        pending = await self.client.xpending_range(
            self.STREAM, self.GROUP, min="-", max="+", count=100,
        )
        for entry in pending:
            msg_id = entry["message_id"]
            delivery_count = entry["times_delivered"]
            idle = entry["time_since_delivered"]
            if idle < idle_ms:
                continue
            if delivery_count >= self.MAX_RETRIES:
                # Move to dead-letter queue
                messages = await self.client.xrange(self.STREAM, min=msg_id, max=msg_id)
                if messages:
                    _, fields = messages[0]
                    await self.client.xadd(self.DLQ_STREAM, fields)
                await self.client.xack(self.STREAM, self.GROUP, msg_id)
                moved += 1
            else:
                # Re-claim for this consumer to retry
                await self.client.xclaim(
                    self.STREAM, self.GROUP, self.consumer,
                    min_idle_time=idle_ms, message_ids=[msg_id],
                )
        return moved

    # ── Monitoring ─────────────────────────────────────────────────────

    async def size(self) -> int:
        return int(await self.client.xlen(self.STREAM))

    async def health(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.aclose()
