from collections import defaultdict, deque
from math import sqrt

from app.core.config import Settings
from app.core.models import AnomalyEvent, MetricPoint, MetricType


class BaseAgent:
    metric_type: MetricType

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.windows: dict[tuple[str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=settings.rolling_window_size)
        )

    def observe(self, metric: MetricPoint) -> AnomalyEvent | None:
        if metric.metric_type != self.metric_type:
            return None

        key = (metric.namespace, metric.pod_name)
        window = self.windows[key]
        if len(window) < 10:
            window.append(metric.metric_value)
            return None

        mean = sum(window) / len(window)
        variance = sum((value - mean) ** 2 for value in window) / len(window)
        stddev = sqrt(variance)
        z_score = 0.0 if stddev == 0 else (metric.metric_value - mean) / stddev
        window.append(metric.metric_value)

        if abs(z_score) <= self.settings.anomaly_zscore_threshold:
            return None

        return AnomalyEvent(
            timestamp=metric.timestamp,
            pod_name=metric.pod_name,
            namespace=metric.namespace,
            metric_type=metric.metric_type,
            metric_value=metric.metric_value,
            severity=self._severity(abs(z_score)),
            z_score=z_score,
        )

    @staticmethod
    def _severity(abs_z_score: float) -> str:
        if abs_z_score >= 4:
            return "critical"
        if abs_z_score >= 3:
            return "high"
        return "medium"


class CPUAgent(BaseAgent):
    metric_type = MetricType.cpu


class MemoryAgent(BaseAgent):
    metric_type = MetricType.memory


class StorageAgent(BaseAgent):
    metric_type = MetricType.disk_io


class NetworkAgent(BaseAgent):
    metric_type = MetricType.network


class LogAgent(BaseAgent):
    metric_type = MetricType.logs


class DetectionAgents:
    def __init__(self, settings: Settings) -> None:
        self.agents = [
            CPUAgent(settings),
            MemoryAgent(settings),
            StorageAgent(settings),
            NetworkAgent(settings),
            LogAgent(settings),
        ]

    def observe(self, metric: MetricPoint) -> list[AnomalyEvent]:
        anomalies: list[AnomalyEvent] = []
        for agent in self.agents:
            anomaly = agent.observe(metric)
            if anomaly is not None:
                anomalies.append(anomaly)
        return anomalies
