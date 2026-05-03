from app.agents.detection import CPUAgent, PVCAgent, RestartAgent, DetectionAgents
from app.core.config import Settings
from app.core.models import MetricPoint, MetricType


def _warm_up(agent, metric_type, count=10, value=10.0):
    """Feed the agent enough values to fill its minimum window."""
    results = []
    values = [value + (i % 3) * 0.1 for i in range(count)]
    for v in values:
        result = agent.observe(
            MetricPoint(pod_name="api", namespace="default", metric_type=metric_type, metric_value=v)
        )
        results.append(result)
    return results


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


def test_cpu_agent_no_anomaly_for_normal_value():
    agent = CPUAgent(Settings())
    _warm_up(agent, MetricType.cpu)
    result = agent.observe(
        MetricPoint(pod_name="api", namespace="default", metric_type=MetricType.cpu, metric_value=10.05)
    )
    assert result is None


def test_cpu_agent_ignores_wrong_metric_type():
    agent = CPUAgent(Settings())
    result = agent.observe(
        MetricPoint(pod_name="api", namespace="default", metric_type=MetricType.memory, metric_value=999)
    )
    assert result is None


def test_pvc_agent_detects_anomaly():
    agent = PVCAgent(Settings())
    _warm_up(agent, MetricType.pvc_usage, value=50.0)
    anomaly = agent.observe(
        MetricPoint(pod_name="db", namespace="default", metric_type=MetricType.pvc_usage, metric_value=500)
    )
    assert anomaly is not None
    assert anomaly.metric_type == MetricType.pvc_usage


def test_restart_agent_detects_anomaly():
    agent = RestartAgent(Settings())
    _warm_up(agent, MetricType.restarts, value=0.0)
    anomaly = agent.observe(
        MetricPoint(pod_name="web", namespace="default", metric_type=MetricType.restarts, metric_value=50)
    )
    assert anomaly is not None
    assert anomaly.metric_type == MetricType.restarts


def test_detection_agents_has_all_types():
    agents = DetectionAgents(Settings())
    types_covered = {a.metric_type for a in agents.agents}
    expected = {MetricType.cpu, MetricType.memory, MetricType.disk_io, MetricType.network, MetricType.logs, MetricType.pvc_usage, MetricType.restarts}
    assert types_covered == expected


def test_severity_levels():
    agent = CPUAgent(Settings())
    _warm_up(agent, MetricType.cpu, value=10.0)
    # Medium: z > 2
    anomaly = agent.observe(
        MetricPoint(pod_name="api", namespace="default", metric_type=MetricType.cpu, metric_value=20)
    )
    assert anomaly is not None
    assert anomaly.severity == "medium"
