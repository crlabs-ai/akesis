import hmac

from fastapi import Header, HTTPException, Request, status

from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger

logger = get_logger("akesis.security")


async def verify_read_token(
    request: Request,
    x_akesis_read_token: str | None = Header(default=None, alias="X-Akesis-Read-Token"),
) -> None:
    """Verifies the X-Akesis-Read-Token header for Read API endpoints.

    Behavior:
    - If read_api_token is not configured (empty string in dev/test), access is permitted.
    - If read_api_token is configured, requests must supply a matching token in constant time.
    - Missing or invalid tokens return 401 Unauthorized with a consistent error message.
    - Never logs or echoes the submitted token.
    """
    active_settings = getattr(request.app.state, "settings", settings)
    configured_token = active_settings.read_api_token
    if not configured_token:
        if active_settings.environment.lower() not in ("production", "prod"):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing read API token"
        )

    if not x_akesis_read_token:
        logger.warning("read_api_access_rejected_missing_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing read API token",
        )

    # Constant-time comparison to prevent timing side-channel attacks
    if not hmac.compare_digest(x_akesis_read_token.strip(), configured_token.strip()):
        logger.warning("read_api_access_rejected_invalid_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing read API token",
        )
