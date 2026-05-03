from app.agents.detection import CPUAgent
from app.core.config import Settings
from app.core.models import MetricPoint, MetricType


def test_cpu_agent_emits_anomaly_for_large_z_score():
    agent = CPUAgent(Settings())
    # Use slightly varying values to produce a non-zero stddev baseline
    warm_up = [10.0, 10.2, 9.8, 10.1, 9.9, 10.0, 10.3, 9.7, 10.1, 9.9]
    for value in warm_up:
        assert agent.observe(MetricPoint(pod_name="api", namespace="default", metric_type=MetricType.cpu, metric_value=value)) is None

    anomaly = agent.observe(MetricPoint(pod_name="api", namespace="default", metric_type=MetricType.cpu, metric_value=100))

    assert anomaly is not None
    assert anomaly.pod_name == "api"
    assert anomaly.metric_type == MetricType.cpu
    assert anomaly.severity in {"medium", "high", "critical"}

