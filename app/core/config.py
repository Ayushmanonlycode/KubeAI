from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "ai-observability-backend"
    environment: str = "development"
    prometheus_url: str = "http://prometheus:9090"
    redis_url: str = "redis://redis:6379/0"
    database_url: str = "postgresql://observability:observability@postgres:5432/observability"
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_model: str = "gemini-2.5-flash"
    metrics_poll_interval_seconds: int = 10
    rolling_window_size: int = 50
    anomaly_zscore_threshold: float = 2.0
    correlation_window_seconds: int = 5
    correlation_threshold: float = 0.8
    graph_path: str = "/data/dependency_graph.json"
    retention_days: int = 7
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_key: str | None = Field(default=None, repr=False)
    cors_origins: list[str] = Field(default=[])
    prometheus_timeout_seconds: float = 5.0
    gemini_timeout_seconds: float = 8.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
