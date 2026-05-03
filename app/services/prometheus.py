from datetime import datetime, timezone

import httpx

from app.core.config import Settings
from app.core.models import MetricPoint, MetricType


PROMQL = {
    MetricType.cpu: 'sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{pod!="",container!="POD"}[2m])) * 100',
    MetricType.memory: 'sum by (namespace,pod) (container_memory_working_set_bytes{pod!="",container!="POD"}) / 1024 / 1024',
    MetricType.disk_io: 'sum by (namespace,pod) (rate(container_fs_reads_bytes_total{pod!="",container!="POD"}[2m]) + rate(container_fs_writes_bytes_total{pod!="",container!="POD"}[2m]))',
    MetricType.network: 'sum by (namespace,pod) (rate(container_network_receive_bytes_total{pod!=""}[2m]) + rate(container_network_transmit_bytes_total{pod!=""}[2m]))',
    MetricType.pvc_usage: 'sum by (namespace,pod) (kubelet_volume_stats_used_bytes) / 1024 / 1024',
    MetricType.restarts: 'sum by (namespace,pod) (kube_pod_container_status_restarts_total)',
}


class PrometheusClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.prometheus_url, timeout=5.0)

    async def query_metric(self, metric_type: MetricType) -> list[MetricPoint]:
        response = await self.client.get("/api/v1/query", params={"query": PROMQL[metric_type]})
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise ValueError(f"prometheus query failed: {payload}")
        timestamp = datetime.now(timezone.utc)
        points: list[MetricPoint] = []
        for result in payload["data"]["result"]:
            metric = result.get("metric", {})
            pod = metric.get("pod")
            namespace = metric.get("namespace", "default")
            if not pod:
                continue
            value = float(result["value"][1])
            points.append(
                MetricPoint(
                    pod_name=pod,
                    namespace=namespace,
                    metric_type=metric_type,
                    metric_value=value,
                    timestamp=timestamp,
                )
            )
        return points

    async def collect_all(self) -> list[MetricPoint]:
        points: list[MetricPoint] = []
        for metric_type in PROMQL:
            points.extend(await self.query_metric(metric_type))
        return points

    async def close(self) -> None:
        await self.client.aclose()
