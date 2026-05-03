"""Tests for API key authentication."""
import secrets
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.auth import require_api_key


def _make_request(api_key_setting: str | None) -> MagicMock:
    """Create a mock Request with settings."""
    request = MagicMock()
    request.app.state.settings.api_key = api_key_setting
    return request


@pytest.mark.asyncio
async def test_auth_skipped_when_no_key_configured():
    """When API_KEY is not set, auth should be skipped."""
    request = _make_request(api_key_setting=None)
    # Should not raise
    await require_api_key(request, key=None)


@pytest.mark.asyncio
async def test_auth_passes_with_correct_key():
    """When API_KEY is set and correct key is provided, auth passes."""
    request = _make_request(api_key_setting="test-key-123")
    await require_api_key(request, key="test-key-123")


@pytest.mark.asyncio
async def test_auth_fails_with_wrong_key():
    """When API_KEY is set and wrong key is provided, should raise 401."""
    from fastapi import HTTPException

    request = _make_request(api_key_setting="test-key-123")
    with pytest.raises(HTTPException) as exc_info:
        await require_api_key(request, key="wrong-key")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_fails_with_no_key_when_required():
    """When API_KEY is set and no key is provided, should raise 401."""
    from fastapi import HTTPException

    request = _make_request(api_key_setting="test-key-123")
    with pytest.raises(HTTPException) as exc_info:
        await require_api_key(request, key=None)
    assert exc_info.value.status_code == 401


def test_constant_time_comparison_used():
    """Verify that secrets module is imported for constant-time comparison."""
    import app.core.auth as auth_module
    import inspect
    source = inspect.getsource(auth_module)
    assert "secrets.compare_digest" in source
