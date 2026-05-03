"""Tests for storage engine startup behavior."""
import pytest

from app.core.config import Settings


def test_storage_raises_on_unreachable_db():
    """start() should raise RuntimeError after all retries fail."""
    from app.services.storage import StorageEngine

    settings = Settings(database_url="postgresql://bad:bad@localhost:1/nonexistent")
    engine = StorageEngine(settings)

    with pytest.raises(RuntimeError, match="Database unavailable after 5 retries"):
        import asyncio
        asyncio.run(engine.start())


def test_storage_init_pool_is_none():
    """Pool should be None before start() is called."""
    from app.services.storage import StorageEngine

    settings = Settings()
    engine = StorageEngine(settings)
    assert engine.pool is None


def test_storage_health_returns_false_without_pool():
    """health() should return False when pool is None."""
    import asyncio
    from app.services.storage import StorageEngine

    settings = Settings()
    engine = StorageEngine(settings)
    result = asyncio.run(engine.health())
    assert result is False
