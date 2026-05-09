"""Cluster-wide SRE incident correlation and intelligence.

Aggregates all active anomalies, metrics, and dependency data across the
entire cluster and produces a prioritised incident report via Gemini.

This is an on-demand analysis triggered by ``GET /cluster/incidents`` —
it does **not** run as a background loop.
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.models import ClusterIncidentReport, CriticalIncident
from app.core.state import RuntimeState
from app.services.correlation import CorrelationEngine
from app.services.gemini import GeminiReasoningEngine
from app.services.storage import StorageEngine

# ── Cache TTL for cluster reports (seconds) ─────────────────────────────
_CACHE_TTL_SECONDS = 60.0

# ── Severity ranking used for deterministic fallback ────────────────────
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# ── Cluster-wide SRE mega-prompt ────────────────────────────────────────
_CLUSTER_PROMPT = """\
You are an elite Kubernetes Site Reliability Engineer (SRE), distributed systems diagnostician, and AI-powered incident response analyst.

Your responsibility is NOT to analyze every anomaly independently. Your responsibility is to identify the MOST CRITICAL operational incidents in the cluster and provide high-value reasoning only for the highest-risk situations.

You are operating inside a real-time Kubernetes observability and incident intelligence platform.

The platform already performs:
- deterministic anomaly detection
- metric collection
- threshold analysis
- restart/crash monitoring
- dependency mapping
- PVC and storage monitoring

Your job is to perform HIGH-LEVEL INCIDENT CORRELATION and generate actionable operational intelligence.

You must think like a senior production SRE responsible for cluster reliability under pressure.

==================================================
SYSTEM CONTEXT
==================================================

Cluster Type:
- Single-node Kubernetes / K3s / Minikube edge cluster

Workload Characteristics:
- Multi-namespace microservices
- Shared node resources
- PVC-backed storage
- Bursty workloads possible
- Resource contention possible

Telemetry Sources:
- CPU metrics
- Memory metrics
- Disk/PVC metrics
- Network metrics
- Restart/crash events
- Kubernetes events
- Pod dependency graph
- Namespace-level correlations

==================================================
PRIMARY OBJECTIVE
==================================================

Analyze the provided incident candidates and determine:

1. Which incidents represent the HIGHEST operational risk
2. Whether multiple anomalies are symptoms of a SINGLE root issue
3. Which workloads/services are causing cascading impact
4. Which incidents require immediate intervention

Focus ONLY on the TOP 5 MOST CRITICAL INCIDENTS.

Ignore low-severity noise and transient spikes.

==================================================
IMPORTANT REASONING RULES
==================================================

- Prefer causal reasoning over surface correlation
- Identify the PRIMARY initiating failure when possible
- Correlate anomalies across pods/namespaces/services
- Detect cascading failures and dependency chains
- Treat repeated restart loops as high severity
- Treat kube-system namespace issues as high priority
- Treat PVC saturation and disk pressure as critical
- Consider whether CPU/memory spikes are causes OR downstream symptoms
- Consider shared-node contention effects
- Consider whether multiple services are competing for the same resources

DO NOT:
- Analyze each pod independently unless necessary
- Generate generic recommendations
- Recommend "check logs"
- Recommend "monitor the system"
- Hallucinate missing metrics
- Assume unavailable data

==================================================
INCIDENT PRIORITIZATION CRITERIA
==================================================

Higher priority if:
- Affects critical namespaces
- Causes cascading failures
- Includes restarts/crash loops
- Shows persistent resource saturation
- Impacts storage/PVC operations
- Affects multiple dependent services
- Indicates memory leak behavior
- Indicates disk exhaustion
- Shows network instability
- Impacts observability/control-plane services

Lower priority if:
- Short transient spikes
- Single isolated low-impact anomaly
- Quickly self-recovered behavior
- Weak/noisy signals

==================================================
CONFIDENCE SCORING
==================================================

High:
- Strong telemetry evidence
- Clear causal chain
- Consistent correlated signals

Medium:
- Likely root cause inferred
- Some ambiguity remains

Low:
- Insufficient/conflicting evidence
- Weak correlations only

Confidence MUST be EXACTLY one of:
- High
- Medium
- Low

==================================================
OUTPUT FORMAT
==================================================

Return ONLY a valid JSON object.

Do NOT return markdown.
Do NOT return explanations outside JSON.
Do NOT return prose outside JSON.

The JSON schema MUST be:

{{
  "cluster_summary": "short overall cluster health summary",
  "critical_incidents": [
    {{
      "rank": 1,
      "incident_title": "short incident title",
      "affected_services": ["svc-a", "svc-b"],
      "affected_pods": ["pod-a", "pod-b"],
      "root_cause": "most probable operational root cause",
      "impact": "operational impact description",
      "confidence": "High",
      "recommendation": "specific actionable mitigation"
    }}
  ]
}}

RULES:
- Maximum 5 incidents
- Minimum 1 incident
- rank must be integer
- confidence must be exactly High, Medium, or Low
- recommendations must be operationally actionable
- recommendations must be specific
- cluster_summary must be concise

==================================================
INPUT INCIDENT DATA
==================================================

{structured_input}
"""


class ClusterAnalyzer:
    """Aggregates cluster-wide telemetry and calls Gemini for incident correlation."""

    def __init__(
        self,
        settings: Settings,
        storage: StorageEngine,
        correlation: CorrelationEngine,
        gemini: GeminiReasoningEngine,
        state: RuntimeState,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.correlation = correlation
        self.gemini = gemini
        self.state = state
        self.logger = get_logger(__name__, "cluster-analyzer")

        # Simple time-based cache to avoid Gemini spam on dashboard refreshes
        self._cached_report: ClusterIncidentReport | None = None
        self._cached_at: float = 0.0

    # ── Public entry point ──────────────────────────────────────────────

    async def analyze(self) -> ClusterIncidentReport:
        """Return a cluster-wide incident report, using cache when fresh."""
        now = monotonic()
        if self._cached_report and (now - self._cached_at) < _CACHE_TTL_SECONDS:
            return self._cached_report

        structured_input = await self._build_snapshot()
        report = await self._call_gemini(structured_input)
        self._cached_report = report
        self._cached_at = monotonic()
        return report

    # ── Snapshot assembly ───────────────────────────────────────────────

    async def _build_snapshot(self) -> dict[str, Any]:
        """Aggregate anomalies, metrics, and dependency edges from the last hour."""
        since = datetime.now(timezone.utc) - timedelta(hours=1)

        raw_anomalies = await self.storage.anomalies_since(since)
        raw_metrics = await self.storage.metrics_since(since)
        dependency_graph = self.correlation.export_json()

        # ── Group anomalies by namespace/pod ────────────────────────────
        anomaly_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for anomaly in raw_anomalies:
            key = f"{anomaly['namespace']}/{anomaly['pod_name']}"
            anomaly_groups[key].append({
                "metric_type": str(anomaly["metric_type"]),
                "metric_value": round(float(anomaly["metric_value"]), 2),
                "severity": str(anomaly["severity"]),
                "z_score": round(float(anomaly["z_score"]), 2),
                "timestamp": anomaly["timestamp"].isoformat()
                if isinstance(anomaly["timestamp"], datetime)
                else str(anomaly["timestamp"]),
            })

        # ── Group metrics by namespace/pod ──────────────────────────────
        metric_groups: dict[str, dict[str, float]] = defaultdict(dict)
        for metric in raw_metrics:
            key = f"{metric['namespace']}/{metric['pod_name']}"
            mtype = str(metric["metric_type"])
            # Keep the most recent value per metric type
            if mtype not in metric_groups[key]:
                metric_groups[key][mtype] = round(float(metric["metric_value"]), 2)

        # ── Build per-pod summaries ─────────────────────────────────────
        all_pods = set(anomaly_groups.keys()) | set(metric_groups.keys())
        pod_summaries: list[dict[str, Any]] = []
        for pod_key in sorted(all_pods):
            ns, pod_name = pod_key.split("/", 1)
            summary: dict[str, Any] = {
                "pod": pod_name,
                "namespace": ns,
                "anomaly_count": len(anomaly_groups.get(pod_key, [])),
                "anomalies": anomaly_groups.get(pod_key, [])[-10:],  # last 10
                "latest_metrics": metric_groups.get(pod_key, {}),
            }
            pod_summaries.append(summary)

        # ── Extract dependency edges ────────────────────────────────────
        edges = []
        for edge in dependency_graph.get("edges", dependency_graph.get("links", [])):
            edges.append({
                "source": edge.get("source", ""),
                "target": edge.get("target", ""),
                "coefficient": edge.get("coefficient", 0),
                "reason": edge.get("reason", ""),
            })

        return {
            "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
            "window": "last_1_hour",
            "total_anomalies": len(raw_anomalies),
            "total_pods_with_anomalies": len(anomaly_groups),
            "pod_summaries": pod_summaries,
            "dependency_edges": edges,
        }

    # ── Gemini call ─────────────────────────────────────────────────────

    async def _call_gemini(self, structured_input: dict[str, Any]) -> ClusterIncidentReport:
        """Send the cluster snapshot to Gemini and parse the incident report.

        Retries up to ``len(clients)`` times — each attempt rotates to the
        next API key so a 429/quota-exhausted error on one key automatically
        falls through to the next.
        """
        if not self.gemini.configured:
            return self._fallback(structured_input, "Gemini API key is not configured")

        prompt = _CLUSTER_PROMPT.format(structured_input=json.dumps(structured_input, indent=2))
        max_attempts = max(3, len(self.gemini.clients))  # try at least every key

        for attempt in range(max_attempts):
            try:
                await self.gemini._throttle()  # noqa: SLF001 — reuse rate limiter
                response = await asyncio.wait_for(
                    asyncio.to_thread(self._generate, prompt),
                    timeout=self.settings.gemini_timeout_seconds + 10,  # cluster analysis takes longer
                )
                incidents = [
                    CriticalIncident(**inc)
                    for inc in response.get("critical_incidents", [])[:5]
                ]
                report = ClusterIncidentReport(
                    cluster_summary=str(response.get("cluster_summary", "No summary returned")),
                    critical_incidents=incidents,
                    fallback=False,
                )
                self.state.gemini_ok = True
                self.state.gemini_last_error = None
                self.state.gemini_calls += 1
                self.logger.info(
                    "cluster_analysis_complete",
                    extra={
                        "event": "cluster_analysis_complete",
                        "status": "success",
                        "incidents": len(incidents),
                        "attempt": attempt + 1,
                        "keys_available": len(self.gemini.clients),
                    },
                )
                return report
            except Exception as exc:
                err_str = str(exc).lower()
                self.state.gemini_ok = False
                self.state.gemini_last_error = str(exc)
                self.logger.warning(
                    "cluster_analysis_failed",
                    extra={
                        "event": "cluster_analysis_failed",
                        "status": "retry",
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "error": str(exc),
                    },
                )
                # Rotate key immediately — _generate() calls _get_client()
                # which advances the round-robin, so the next iteration
                # will use the next key automatically.
                if "quota" in err_str or "429" in err_str or "resource" in err_str:
                    # Rate limit / quota — rotate immediately, minimal delay
                    await asyncio.sleep(1)
                else:
                    # Other error — backoff
                    await asyncio.sleep(min(2 ** attempt, 5))

        self.state.error_count += 1
        return self._fallback(structured_input, self.state.gemini_last_error or "Gemini unavailable")

    def _generate(self, prompt: str) -> dict[str, Any]:
        """Synchronous Gemini call with structured JSON output."""
        from google.genai import types

        client = self.gemini._get_client()  # noqa: SLF001
        if not client:
            raise ValueError("No Gemini clients configured")

        response = client.models.generate_content(
            model=self.settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema={
                    "type": "object",
                    "properties": {
                        "cluster_summary": {"type": "string"},
                        "critical_incidents": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "rank": {"type": "integer"},
                                    "incident_title": {"type": "string"},
                                    "affected_services": {"type": "array", "items": {"type": "string"}},
                                    "affected_pods": {"type": "array", "items": {"type": "string"}},
                                    "root_cause": {"type": "string"},
                                    "impact": {"type": "string"},
                                    "confidence": {"type": "string"},
                                    "recommendation": {"type": "string"},
                                },
                                "required": [
                                    "rank", "incident_title", "affected_services",
                                    "affected_pods", "root_cause", "impact",
                                    "confidence", "recommendation",
                                ],
                            },
                        },
                    },
                    "required": ["cluster_summary", "critical_incidents"],
                },
            ),
        )
        return response.parsed or {}

    # ── Deterministic fallback ──────────────────────────────────────────

    def _fallback(self, structured_input: dict[str, Any], reason: str) -> ClusterIncidentReport:
        """Generate a deterministic incident report without Gemini."""
        pod_summaries = structured_input.get("pod_summaries", [])

        # Score each pod by (max_severity_rank, max_z_score, anomaly_count)
        scored: list[tuple[float, dict[str, Any]]] = []
        for pod in pod_summaries:
            anomalies = pod.get("anomalies", [])
            if not anomalies:
                continue
            max_sev = max(_SEVERITY_RANK.get(a.get("severity", "low").lower(), 0) for a in anomalies)
            max_z = max(abs(a.get("z_score", 0)) for a in anomalies)
            count = len(anomalies)
            # Composite score: severity weight (×100) + z_score (×10) + count
            score = max_sev * 100 + max_z * 10 + count
            scored.append((score, pod))

        # Sort descending by composite score, take top 5
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:5]

        incidents: list[CriticalIncident] = []
        for rank, (_, pod) in enumerate(top, start=1):
            anomalies = pod.get("anomalies", [])
            metric_types = sorted({a.get("metric_type", "unknown") for a in anomalies})
            severities = sorted({a.get("severity", "unknown") for a in anomalies})
            pod_name = pod.get("pod", "unknown")
            namespace = pod.get("namespace", "unknown")

            incidents.append(CriticalIncident(
                rank=rank,
                incident_title=f"{', '.join(metric_types)} anomaly on {namespace}/{pod_name}",
                affected_services=[pod_name],
                affected_pods=[f"{namespace}/{pod_name}"],
                root_cause=(
                    f"Deterministic detection found {len(anomalies)} anomalies "
                    f"({', '.join(metric_types)}) with severities: {', '.join(severities)}. "
                    f"AI reasoning unavailable: {reason}"
                ),
                impact=f"Pod {pod_name} in namespace {namespace} exhibiting abnormal {', '.join(metric_types)} behavior.",
                confidence="Low",
                recommendation="Inspect pod resource limits, recent restarts, node pressure, and correlated dependency edges.",
            ))

        if not incidents:
            incidents.append(CriticalIncident(
                rank=1,
                incident_title="No active anomalies detected",
                affected_services=[],
                affected_pods=[],
                root_cause="No anomalies found in the last 1 hour.",
                impact="None — cluster appears healthy.",
                confidence="High",
                recommendation="No action required. Continue monitoring.",
            ))

        total = structured_input.get("total_anomalies", 0)
        return ClusterIncidentReport(
            cluster_summary=f"Deterministic analysis: {total} anomalies across {len(scored)} pods in the last hour. AI correlation unavailable.",
            critical_incidents=incidents,
            fallback=True,
        )
