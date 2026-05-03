import json
from typing import Iterable

import redis.asyncio as redis

from app.core.config import Settings
from app.core.models import MetricPoint


class MetricQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = redis.from_url(settings.redis_url, decode_responses=True)
        self.name = "metrics"

    async def publish_many(self, metrics: Iterable[MetricPoint]) -> None:
        pipe = self.client.pipeline()
        count = 0
        for metric in metrics:
            pipe.rpush(self.name, metric.model_dump_json())
            count += 1
        if count:
            await pipe.execute()

    async def pop(self, timeout: int = 2) -> MetricPoint | None:
        item = await self.client.blpop(self.name, timeout=timeout)
        if item is None:
            return None
        _, payload = item
        return MetricPoint.model_validate_json(payload)

    async def size(self) -> int:
        return int(await self.client.llen(self.name))

    async def health(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.aclose()
