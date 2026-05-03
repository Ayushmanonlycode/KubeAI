import json
from collections import defaultdict, deque
from math import sqrt
from pathlib import Path
from typing import Any

import networkx as nx

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.models import AnomalyEvent


class CorrelationEngine:
    """Builds a dependency graph from temporally correlated anomaly events.

    **Important:** This engine writes graph state to a single JSON file.
    It must run in exactly one worker process (the ``app.worker`` entrypoint)
    to prevent write races. The API process reads the graph via
    ``export_json()`` but never writes to the file.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.graph = nx.DiGraph()
        self.events: deque[AnomalyEvent] = deque(maxlen=1000)
        self.series: dict[tuple[str, str], deque[float]] = defaultdict(lambda: deque(maxlen=50))
        self.path = Path(settings.graph_path)
        self.logger = get_logger(__name__, "correlation")
        self.reload()

    def observe(self, event: AnomalyEvent) -> list[tuple[str, str]]:
        created_edges: list[tuple[str, str]] = []
        event_key = (event.namespace, event.pod_name)
        self.series[event_key].append(event.metric_value)

        for previous in list(self.events):
            if abs((event.timestamp - previous.timestamp).total_seconds()) > self.settings.correlation_window_seconds:
                continue
            previous_key = (previous.namespace, previous.pod_name)
            coefficient = self._correlation(self.series[previous_key], self.series[event_key])
            if coefficient > self.settings.correlation_threshold and previous_key != event_key:
                source = self._node(previous)
                target = self._node(event)
                self.graph.add_edge(
                    source,
                    target,
                    coefficient=coefficient,
                    last_seen=event.timestamp.isoformat(),
                    reason="anomalies within 5 seconds and correlated metric movement",
                )
                created_edges.append((source, target))

        self.events.append(event)
        if created_edges:
            self.persist()
        return created_edges

    def export_json(self) -> dict[str, Any]:
        return nx.node_link_data(self.graph, edges="edges")

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.export_json(), indent=2), encoding="utf-8")
        self.logger.info("graph_persisted", extra={"event": "graph_persisted", "status": "success"})

    def reload(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.graph = nx.node_link_graph(payload, edges="edges")
            self.logger.info("graph_reloaded", extra={"event": "graph_reloaded", "status": "success"})
        except Exception as exc:
            self.logger.error("graph_reload_failed", extra={"event": "graph_reload_failed", "status": "error", "error": str(exc)})

    @staticmethod
    def _node(event: AnomalyEvent) -> str:
        return f"{event.namespace}/{event.pod_name}"

    @staticmethod
    def _correlation(left: deque[float], right: deque[float]) -> float:
        size = min(len(left), len(right))
        if size < 3:
            return 0.0
        xs = list(left)[-size:]
        ys = list(right)[-size:]
        mean_x = sum(xs) / size
        mean_y = sum(ys) / size
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        denom_x = sqrt(sum((x - mean_x) ** 2 for x in xs))
        denom_y = sqrt(sum((y - mean_y) ** 2 for y in ys))
        if denom_x == 0 or denom_y == 0:
            return 0.0
        return numerator / (denom_x * denom_y)
