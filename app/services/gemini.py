import asyncio
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
        self.client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None
        self.logger = get_logger(__name__, "gemini")

    @property
    def configured(self) -> bool:
        return self.client is not None

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
        if self.client is None:
            return {
                "status": "not_configured",
                "model": self.settings.gemini_model,
                "error": "Gemini API key is not configured",
            }
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.settings.gemini_model,
                    contents="Return the single word ok.",
                ),
                timeout=8,
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
        if self.client is None:
            return self._fallback(pod, namespace, events, "Gemini API key is not configured")

        structured_input = {
            "pod": pod,
            "namespace": namespace,
            "events": [self._event_summary(event) for event in events],
            "metrics": {metric.metric_type.value: metric.metric_value for metric in metrics[-10:]},
        }
        prompt = (
            "Analyze these Kubernetes pod events. Return JSON only with root_cause, "
            "confidence, and recommendation. Be concise and operationally specific.\n"
            f"{structured_input}"
        )

        for attempt in range(3):
            try:
                response = await asyncio.wait_for(asyncio.to_thread(self._generate, prompt), timeout=8)
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

    def _generate(self, prompt: str) -> dict[str, Any]:
        response = self.client.models.generate_content(
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
