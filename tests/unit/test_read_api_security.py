"""API security configuration: sessions replace the retired global read token (ADR-0012)."""

import base64

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.apps.api.main import create_app
from src.packages.shared.config import Settings, settings

FERNET_KEY = base64.urlsafe_b64encode(b"\x01" * 32).decode()  # synthetic, valid shape


def test_read_token_is_gone() -> None:
    assert "read_api_token" not in Settings.model_fields
    default_headers = Settings.model_fields["cors_allowed_headers"].default_factory
    assert default_headers is not None
    assert "X-Akesis-Read-Token" not in default_headers()  # type: ignore[call-arg]


def test_global_read_routes_no_longer_exist() -> None:
    client = TestClient(create_app(app_settings=settings))
    for path in ("/v1/incidents", "/v1/pipelines", "/v1/webhook-deliveries"):
        assert client.get(path, headers={"X-Akesis-Read-Token": "anything"}).status_code == 404


def test_tenant_routes_require_a_session_even_in_development() -> None:
    client = TestClient(create_app(app_settings=Settings(environment="development")))
    for path in ("incidents", "incidents/inc_x", "pipelines", "deliveries", "members"):
        response = client.get(f"/v1/workspaces/ws_any/{path}")
        assert response.status_code == 401
        assert response.json() == {"detail": "not_signed_in"}
    assert client.get("/v1/me").status_code == 401


def test_health_endpoints_remain_accessible_independently() -> None:
    """5. Health endpoints remain accessible without a session."""
    app = create_app(app_settings=settings)
    client = TestClient(app)

    live = client.get("/health/liveness")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}

    ready = client.get("/health/readiness")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_cors_allowed_origins() -> None:
    """8. Allowed CORS origins receive Access-Control-Allow-Origin."""
    custom_settings = Settings(
        cors_allowed_origins=["http://localhost:3000", "https://app.akesis.dev"],
        trusted_hosts=["testserver", "localhost", "test"],
    )
    app = create_app(app_settings=custom_settings)
    client = TestClient(app)

    # Test allowed origin
    res = client.get(
        "/health/liveness",
        headers={"Origin": "http://localhost:3000"},
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_disallowed_origins() -> None:
    """9. Disallowed origins do not receive Access-Control-Allow-Origin."""
    custom_settings = Settings(
        cors_allowed_origins=["http://localhost:3000"],
        trusted_hosts=["testserver", "localhost", "test"],
    )
    app = create_app(app_settings=custom_settings)
    client = TestClient(app)

    # Test disallowed origin
    res = client.get(
        "/health/liveness",
        headers={"Origin": "http://evil-attacker.com"},
    )
    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers


def test_cors_preflight_handling() -> None:
    """10. CORS preflight handles permitted headers and methods correctly."""
    custom_settings = Settings(
        cors_allowed_origins=["http://localhost:3000"],
        cors_allowed_methods=["GET", "POST", "OPTIONS"],
        cors_allowed_headers=["Content-Type", "X-Akesis-CSRF"],
        trusted_hosts=["testserver", "localhost", "test"],
    )
    app = create_app(app_settings=custom_settings)
    client = TestClient(app)

    res = client.options(
        "/v1/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Akesis-CSRF",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "GET" in res.headers.get("access-control-allow-methods", "")
    assert "x-akesis-csrf" in res.headers.get("access-control-allow-headers", "").lower()


def test_trusted_hosts_rejects_invalid_host() -> None:
    """11. Invalid Host headers are rejected with HTTP 400."""
    custom_settings = Settings(
        trusted_hosts=["api.akesis.dev", "testserver"],
    )
    app = create_app(app_settings=custom_settings)
    client = TestClient(app)

    # Host not in trusted list
    res = client.get(
        "/health/liveness",
        headers={"Host": "untrusted-host.com"},
    )
    assert res.status_code == 400
    assert "invalid host" in res.text.lower()


def test_trusted_hosts_accepts_valid_host() -> None:
    """12. Valid configured Host headers work."""
    custom_settings = Settings(
        trusted_hosts=["api.akesis.dev", "testserver"],
    )
    app = create_app(app_settings=custom_settings)
    client = TestClient(app)

    res = client.get(
        "/health/liveness",
        headers={"Host": "api.akesis.dev"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_production_config_rejects_missing_or_placeholder_secrets() -> None:
    """13. Production configuration rejects missing or placeholder secrets."""
    # Attempting to create production settings with development placeholders
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            environment="production",
            database_url="postgresql+asyncpg://postgres:postgrespassword@localhost:5436/akesis",
            github_webhook_secret="test_webhook_secret",
            gemini_api_key="your_gemini_api_key_here",
            slack_webhook_url="https://hooks.slack.com/services/...",
            slack_signing_secret="test_slack_signing_secret",
            cors_allowed_origins=["https://dashboard.akesis.dev"],
            trusted_hosts=["api.akesis.dev"],
        )

    err_str = str(exc_info.value)
    assert "DATABASE_URL contains default/development credentials" in err_str
    assert "GITHUB_WEBHOOK_SECRET is missing or using an insecure test placeholder" in err_str
    assert "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are required in production" in err_str
    # Phase 1.14c: not required by either service yet (no runtime caller), placeholders still fail.
    assert "GEMINI_API_KEY is an insecure placeholder" in err_str
    assert "SLACK_WEBHOOK_URL is an insecure placeholder" in err_str
    assert "SLACK_SIGNING_SECRET is missing or using an insecure placeholder" in err_str


def test_production_config_rejects_wildcard_cors_and_hosts() -> None:
    """14. Production configuration rejects wildcard CORS and Trusted Hosts."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            environment="production",
            database_url="postgresql+asyncpg://prod_user:SecurePass123!@prod-db.internal:5432/akesis_prod",
            github_webhook_secret="prod_sec_abc1234567890",
            github_app_id=12345,
            github_app_private_key="synthetic-app-key-material",
            gemini_api_key="AIzaSyProdKey1234567890",
            slack_webhook_url="https://hooks.slack.com/services/T01/B02/realprodsecret",
            slack_signing_secret="prod_slack_sec_1234567890",
            cors_allowed_origins=["*"],
            trusted_hosts=["*"],
        )

    err_str = str(exc_info.value)
    assert "CORS_ALLOWED_ORIGINS must not contain wildcard" in err_str
    assert "TRUSTED_HOSTS must not contain wildcard" in err_str


def test_production_config_valid() -> None:
    """Production config passes when all parameters are valid and distinct."""
    prod_settings = Settings(**_valid_production())  # type: ignore[arg-type]
    assert prod_settings.environment == "production"


def _valid_production(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "environment": "production",
        "database_url": "postgresql+asyncpg://prod_user:SecurePass123!@prod-db.internal:5432/akesis_prod",
        "github_webhook_secret": "prod_sec_abc1234567890",
        "github_app_id": 12345,
        "github_app_private_key": "synthetic-app-key-material",
        "gemini_api_key": "AIzaSyProdKey1234567890",
        "slack_webhook_url": "https://hooks.slack.com/services/T01/B02/realprodsecret",
        "slack_signing_secret": "prod_slack_sec_1234567890",
        "cors_allowed_origins": ["https://dashboard.akesis.dev"],
        "trusted_hosts": ["api.akesis.dev"],
        "github_app_client_id": "Iv1.synthetic0000",
        "github_app_client_secret": "synthetic-client-secret-0123456789",
        "auth_redirect_uri": "https://dashboard.akesis.dev/auth/callback",
        "auth_token_encryption_key": FERNET_KEY,
        "slack_approver_user_ids": ["U_TEST"],
        "slack_workspace_id": "T_TEST",
        "slack_channel_id": "C_TEST",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "overrides",
    [{"github_app_id": 0}, {"github_app_private_key": ""}, {"github_app_private_key": "  "}],
)
def test_production_config_requires_github_app_credentials(overrides: dict[str, object]) -> None:
    """Phase 1.8: every GitHub call uses the App, so production needs its ID and private key."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_valid_production(**overrides))  # type: ignore[arg-type]
    assert "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are required in production" in str(
        exc_info.value
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"github_app_client_id": ""}, "GITHUB_APP_CLIENT_ID and GITHUB_APP_CLIENT_SECRET"),
        ({"github_app_client_secret": " "}, "GITHUB_APP_CLIENT_ID and GITHUB_APP_CLIENT_SECRET"),
        ({"auth_redirect_uri": "http://dashboard.akesis.dev/auth/callback"}, "AUTH_REDIRECT_URI"),
        ({"auth_redirect_uri": "https://dashboard.akesis.dev/elsewhere"}, "AUTH_REDIRECT_URI"),
        ({"auth_cookie_secure": False}, "AUTH_COOKIE_SECURE must be true"),
        ({"auth_token_encryption_key": ""}, "AUTH_TOKEN_ENCRYPTION_KEY must be a valid Fernet"),
        ({"auth_token_encryption_key": "not-a-key"}, "AUTH_TOKEN_ENCRYPTION_KEY must be a valid"),
        (
            {"github_app_client_secret": "prod_slack_sec_1234567890"},
            "Sign-in secrets must be distinct",
        ),
    ],
)
def test_production_config_requires_safe_sign_in(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_valid_production(**overrides))  # type: ignore[arg-type]
    assert message in str(exc_info.value)


def test_personal_access_token_setting_no_longer_exists() -> None:
    """An old GITHUB_TOKEN in the environment is ignored; nothing can read it."""
    assert "github_token" not in Settings.model_fields
