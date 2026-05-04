import asyncio
import hashlib
import itertools
import json
from time import monotonic
from typing import Any

from google import genai
from google.genai import types

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.models import AnomalyEvent, Insight, MetricPoint
from app.core.state import RuntimeState


class GeminiReasoningEngine:
    def __init__(self, settings: Settings, state: RuntimeState) -> None:
        self.settings = settings
        self.state = state
        self.clients = []
        if settings.gemini_api_key:
            keys = [k.strip() for k in settings.gemini_api_key.split(",") if k.strip()]
            self.clients = [genai.Client(api_key=k) for k in keys]
        self.client_cycle = itertools.cycle(self.clients) if self.clients else None
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._cache: dict[str, Insight] = {}
        self.logger = get_logger(__name__, "gemini")

    @property
    def configured(self) -> bool:
        return len(self.clients) > 0

    def _get_client(self) -> genai.Client | None:
        return next(self.client_cycle) if self.client_cycle else None

    def status(self) -> str:
        if not self.configured:
            return "not_configured"
        if self.state.gemini_last_error:
            return "error"
        if self.state.gemini_calls == 0:
            return "configured_not_called"
        return "ok" if self.state.gemini_ok else "fallback"

    async def test(self) -> dict[str, Any]:
        started = monotonic()
        client = self._get_client()
        if client is None:
            return {
                "status": "not_configured",
                "model": self.settings.gemini_model,
                "error": "Gemini API key is not configured",
            }
        try:
            await self._throttle()
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=self.settings.gemini_model,
                    contents="Return the single word ok.",
                ),
                timeout=self.settings.gemini_timeout_seconds,
            )
            self.state.gemini_ok = True
            self.state.gemini_last_error = None
            self.state.gemini_calls += 1
            self.state.total_gemini_latency_ms += (monotonic() - started) * 1000
            return {
                "status": "ok",
                "model": self.settings.gemini_model,
                "latency_ms": round((monotonic() - started) * 1000, 2),
                "response": response.text,
            }
        except Exception as exc:
            self.state.gemini_ok = False
            self.state.gemini_last_error = str(exc)
            self.state.error_count += 1
            return {
                "status": "error",
                "model": self.settings.gemini_model,
                "error": str(exc),
            }

    async def analyze(self, pod: str, namespace: str, events: list[AnomalyEvent], metrics: list[MetricPoint]) -> Insight:
        started = monotonic()
        if not self.configured:
            return self._fallback(pod, namespace, events, "Gemini API key is not configured")
        cache_key = self._cache_key(pod, namespace, events)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(update={"timestamp": cached.timestamp})

        structured_input = {
            "pod": pod,
            "namespace": namespace,
            "events": [self._event_summary(event) for event in events],
            "metrics": {metric.metric_type.value: metric.metric_value for metric in metrics[-10:]},
        }
        prompt = f"""
You are a senior Kubernetes Site Reliability Engineer (SRE) responsible for diagnosing production incidents in containerized systems.

Your task is to analyze structured telemetry and anomaly events for a single Kubernetes pod and determine the most probable operational root cause.

Use evidence from the telemetry timeline, resource metrics, and system events. Prefer causal explanations over correlations. Avoid speculation beyond the provided data.

---

ANALYSIS OBJECTIVES

1. Identify the most likely root cause of the performance degradation or failure.
2. Base your reasoning on observable signals such as:
   - CPU, memory, disk, and network behavior
   - restart counts
   - latency or error spikes
   - resource saturation or throttling
   - dependency failures (e.g., database, PVC, network)
3. If multiple anomalies exist, determine the primary initiating event.
4. Provide a concrete operational recommendation that an on-call engineer can execute immediately.

---

DECISION RULES

- Do not assume missing data.
- If evidence is weak or ambiguous, lower the confidence level.
- If the telemetry indicates normal behavior, state that explicitly.
- Avoid generic advice such as "check logs" or "monitor the system."
- Recommendations must be actionable infrastructure steps.

---

CONFIDENCE CRITERIA

High:
Clear causal relationship supported by strong telemetry signals.

Medium:
Likely cause inferred from correlated signals, but alternative causes remain plausible.

Low:
Insufficient or conflicting evidence.

---

OUTPUT REQUIREMENTS

Return ONLY a valid JSON object.

The JSON must contain exactly these keys:

root_cause
confidence
recommendation

Do not include explanations, markdown, or additional fields.

---

SYSTEM CONTEXT

Cluster type: single-node edge cluster
Workload type: stateless API
Critical dependency: PostgreSQL
SLO: 200ms latency

---

TELEMETRY DATA

{structured_input}
"""

        for attempt in range(3):
            try:
                await self._throttle()
                response = await asyncio.wait_for(
                    asyncio.to_thread(self._generate, prompt),
                    timeout=self.settings.gemini_timeout_seconds,
                )
                insight = Insight(
                    pod=pod,
                    namespace=namespace,
                    root_cause=str(response.get("root_cause", "No root cause returned")),
                    confidence=str(response.get("confidence", "Low")),
                    recommendation=str(response.get("recommendation", "Review recent anomalies and resource limits")),
                    fallback=False,
                    source_events=[event.model_dump(mode="json") for event in events],
                )
                self.state.gemini_ok = True
                self.state.gemini_last_error = None
                self.state.gemini_calls += 1
                self.state.total_gemini_latency_ms += (monotonic() - started) * 1000
                self._store_cache(cache_key, insight)
                return insight
            except Exception as exc:
                self.state.gemini_ok = False
                self.state.gemini_last_error = str(exc)
                self.logger.warning(
                    "gemini_request_failed",
                    extra={"event": "gemini_request_failed", "status": "retry", "attempt": attempt + 1, "error": str(exc)},
                )
                await asyncio.sleep(min(2**attempt, 5))

        self.state.error_count += 1
        return self._fallback(pod, namespace, events, self.state.gemini_last_error or "Gemini unavailable")

    def rules_insight(self, pod: str, namespace: str, events: list[AnomalyEvent]) -> Insight:
        return self._fallback(pod, namespace, events, "severity below AI threshold")

    async def _throttle(self) -> None:
        rpm = max(1, self.settings.gemini_requests_per_minute)
        min_interval = 60.0 / rpm
        async with self._request_lock:
            elapsed = monotonic() - self._last_request_at
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_request_at = monotonic()

    def _store_cache(self, cache_key: str, insight: Insight) -> None:
        if self.settings.insight_cache_size <= 0:
            return
        if len(self._cache) >= self.settings.insight_cache_size:
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key, None)
        self._cache[cache_key] = insight

    @staticmethod
    def _cache_key(pod: str, namespace: str, events: list[AnomalyEvent]) -> str:
        signature = {
            "pod": pod,
            "namespace": namespace,
            "events": [
                {
                    "metric_type": event.metric_type.value,
                    "severity": event.severity,
                    "z_score": round(event.z_score, 1),
                    "value": round(event.metric_value, 1),
                }
                for event in events[-10:]
            ],
        }
        encoded = json.dumps(signature, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _generate(self, prompt: str) -> dict[str, Any]:
        client = self._get_client()
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
                        "root_cause": {"type": "string"},
                        "confidence": {"type": "string"},
                        "recommendation": {"type": "string"},
                    },
                    "required": ["root_cause", "confidence", "recommendation"],
                },
            ),
        )
        return response.parsed or {}

    @staticmethod
    def _event_summary(event: AnomalyEvent) -> str:
        return f"{event.metric_type.value} anomaly value={event.metric_value:.2f} severity={event.severity}"

    @staticmethod
    def _fallback(pod: str, namespace: str, events: list[AnomalyEvent], reason: str) -> Insight:
        event_names = ", ".join(sorted({event.metric_type.value for event in events})) or "unknown"
        return Insight(
            pod=pod,
            namespace=namespace,
            root_cause=f"Deterministic anomaly detection found abnormal {event_names}; AI reasoning unavailable: {reason}",
            confidence="Low",
            recommendation="Inspect pod resource limits, recent restarts, node pressure, and correlated dependency edges.",
            fallback=True,
            source_events=[event.model_dump(mode="json") for event in events],
        )
