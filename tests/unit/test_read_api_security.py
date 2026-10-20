import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.apps.api.main import create_app
from src.packages.shared.config import Settings, settings


def test_read_endpoints_accessible_in_dev_without_token() -> None:
    """1. Read endpoints remain accessible when read_api_token is empty."""
    custom_settings = Settings(
        read_api_token="",
        trusted_hosts=["testserver", "localhost", "test"],
    )
    app = create_app(app_settings=custom_settings)
    client = TestClient(app)

    res_incidents = client.get("/v1/incidents")
    assert res_incidents.status_code == 200

    res_pipelines = client.get("/v1/pipelines")
    assert res_pipelines.status_code == 200


def test_missing_read_token_returns_401_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """2. Missing read token returns 401 when read_api_token is configured."""
    monkeypatch.setattr(settings, "read_api_token", "secret_read_token_123")
    app = create_app(app_settings=settings)
    client = TestClient(app)

    # Missing header
    res_inc = client.get("/v1/incidents")
    assert res_inc.status_code == 401
    assert res_inc.json()["detail"] == "Invalid or missing read API token"

    res_pipe = client.get("/v1/pipelines")
    assert res_pipe.status_code == 401
    assert res_pipe.json()["detail"] == "Invalid or missing read API token"

    res_detail = client.get("/v1/incidents/inc_test_123")
    assert res_detail.status_code == 401
    assert res_detail.json()["detail"] == "Invalid or missing read API token"


def test_incorrect_read_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """3. Incorrect read token returns 401."""
    monkeypatch.setattr(settings, "read_api_token", "secret_read_token_123")
    app = create_app(app_settings=settings)
    client = TestClient(app)

    headers = {"X-Akesis-Read-Token": "wrong_token"}
    res = client.get("/v1/incidents", headers=headers)
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid or missing read API token"
    # Ensure submitted token is not echoed in error response
    assert "wrong_token" not in res.text


def test_valid_read_token_permits_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """4. Valid read token permits access to all read endpoints."""
    monkeypatch.setattr(settings, "read_api_token", "secret_read_token_123")
    app = create_app(app_settings=settings)
    client = TestClient(app)

    headers = {"X-Akesis-Read-Token": "secret_read_token_123"}
    res_inc = client.get("/v1/incidents", headers=headers)
    assert res_inc.status_code == 200

    res_pipe = client.get("/v1/pipelines", headers=headers)
    assert res_pipe.status_code == 200

    # 404 for unknown incident (authenticated but item does not exist)
    res_detail = client.get("/v1/incidents/inc_unknown_404", headers=headers)
    assert res_detail.status_code == 404


def test_health_endpoints_remain_accessible_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5. Health endpoints remain accessible independently of read authentication."""
    monkeypatch.setattr(settings, "read_api_token", "secret_read_token_123")
    app = create_app(app_settings=settings)
    client = TestClient(app)

    # Health endpoints do not require X-Akesis-Read-Token
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
        cors_allowed_headers=["Content-Type", "X-Akesis-Read-Token"],
        trusted_hosts=["testserver", "localhost", "test"],
    )
    app = create_app(app_settings=custom_settings)
    client = TestClient(app)

    res = client.options(
        "/v1/incidents",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Akesis-Read-Token",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "GET" in res.headers.get("access-control-allow-methods", "")
    assert "x-akesis-read-token" in res.headers.get("access-control-allow-headers", "").lower()


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
            github_token="your_github_token_here",
            gemini_api_key="your_gemini_api_key_here",
            slack_webhook_url="https://hooks.slack.com/services/...",
            slack_signing_secret="test_slack_signing_secret",
            cors_allowed_origins=["https://dashboard.akesis.dev"],
            trusted_hosts=["api.akesis.dev"],
        )

    err_str = str(exc_info.value)
    assert "DATABASE_URL contains default/development credentials" in err_str
    assert "GITHUB_WEBHOOK_SECRET is missing or using an insecure test placeholder" in err_str
    assert "GITHUB_TOKEN is required in production" in err_str
    assert "GEMINI_API_KEY is required in production" in err_str
    assert "SLACK_WEBHOOK_URL is missing or using an insecure placeholder" in err_str
    assert "SLACK_SIGNING_SECRET is missing or using an insecure placeholder" in err_str


def test_production_config_rejects_wildcard_cors_and_hosts() -> None:
    """14. Production configuration rejects wildcard CORS and Trusted Hosts."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            environment="production",
            database_url="postgresql+asyncpg://prod_user:SecurePass123!@prod-db.internal:5432/akesis_prod",
            github_webhook_secret="prod_sec_abc1234567890",
            github_token="ghp_prodtoken1234567890",
            gemini_api_key="AIzaSyProdKey1234567890",
            slack_webhook_url="https://hooks.slack.com/services/T01/B02/realprodsecret",
            slack_signing_secret="prod_slack_sec_1234567890",
            cors_allowed_origins=["*"],
            trusted_hosts=["*"],
        )

    err_str = str(exc_info.value)
    assert "CORS_ALLOWED_ORIGINS must not contain wildcard" in err_str
    assert "TRUSTED_HOSTS must not contain wildcard" in err_str


def test_production_config_rejects_read_token_secret_reuse() -> None:
    """14b. Production config rejects read_api_token equaling other integration secrets."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            environment="production",
            database_url="postgresql+asyncpg://prod_user:SecurePass123!@prod-db.internal:5432/akesis_prod",
            github_webhook_secret="shared_secret_1234567890",
            github_token="ghp_prodtoken1234567890",
            gemini_api_key="AIzaSyProdKey1234567890",
            slack_webhook_url="https://hooks.slack.com/services/T01/B02/realprodsecret",
            slack_signing_secret="prod_slack_sec_1234567890",
            cors_allowed_origins=["https://dashboard.akesis.dev"],
            trusted_hosts=["api.akesis.dev"],
            read_api_token="shared_secret_1234567890",  # Reused secret!
        )

    err_str = str(exc_info.value)
    assert "READ_API_TOKEN must be distinct" in err_str


def test_production_config_valid() -> None:
    """Production config passes when all parameters are valid and distinct."""
    prod_settings = Settings(
        environment="production",
        database_url="postgresql+asyncpg://prod_user:SecurePass123!@prod-db.internal:5432/akesis_prod",
        github_webhook_secret="prod_sec_abc1234567890",
        github_token="ghp_prodtoken1234567890",
        gemini_api_key="AIzaSyProdKey1234567890",
        slack_webhook_url="https://hooks.slack.com/services/T01/B02/realprodsecret",
        slack_signing_secret="prod_slack_sec_1234567890",
        cors_allowed_origins=["https://dashboard.akesis.dev"],
        trusted_hosts=["api.akesis.dev"],
        read_api_token="prod_read_token_distinct_987654",
    )
    assert prod_settings.environment == "production"
    assert prod_settings.read_api_token == "prod_read_token_distinct_987654"
