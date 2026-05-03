from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    request: Request,
    key: str | None = Depends(_header),
) -> None:
    """Enforce API-key authentication when ``settings.api_key`` is set.

    If no API key is configured the check is skipped so that local
    development works without extra setup.
    """
    expected: str | None = request.app.state.settings.api_key
    if expected is None:
        return
    if key is None or key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
