from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MetricType(StrEnum):
    cpu = "cpu"
    memory = "memory"
    disk_io = "disk_io"
    network = "network"
    pvc_usage = "pvc_usage"
    restarts = "restarts"
    logs = "logs"


class MetricPoint(BaseModel):
    pod_name: str
    namespace: str
    metric_type: MetricType
    metric_value: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AnomalyEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pod_name: str
    namespace: str
    metric_type: MetricType
    metric_value: float
    severity: str
    z_score: float


class Insight(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pod: str
    namespace: str
    root_cause: str
    confidence: str
    recommendation: str
    fallback: bool = False
    source_events: list[dict[str, Any]] = Field(default_factory=list)
