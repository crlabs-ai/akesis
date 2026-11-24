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


def test_production_missing_token_fails_closed() -> None:
    """Even an incorrectly mutated settings object must not expose incident detail."""
    custom_settings = Settings(environment="test", read_api_token="")
    custom_settings.environment = "production"
    app = create_app(app_settings=custom_settings)
    client = TestClient(app)
    assert client.get("/v1/incidents/inc_sensitive").status_code == 401
    assert client.get("/v1/pipelines").status_code == 401
    assert client.get("/health/liveness").status_code == 200


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


def test_production_config_rejects_read_token_secret_reuse() -> None:
    """14b. Production config rejects read_api_token equaling other integration secrets."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            environment="production",
            database_url="postgresql+asyncpg://prod_user:SecurePass123!@prod-db.internal:5432/akesis_prod",
            github_webhook_secret="shared_secret_1234567890",
            github_app_id=12345,
            github_app_private_key="synthetic-app-key-material",
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
        github_app_id=12345,
        github_app_private_key="synthetic-app-key-material",
        gemini_api_key="AIzaSyProdKey1234567890",
        slack_webhook_url="https://hooks.slack.com/services/T01/B02/realprodsecret",
        slack_signing_secret="prod_slack_sec_1234567890",
        cors_allowed_origins=["https://dashboard.akesis.dev"],
        trusted_hosts=["api.akesis.dev"],
        read_api_token="prod_read_token_distinct_987654",
        slack_approver_user_ids=["U_TEST"],
        slack_workspace_id="T_TEST",
        slack_channel_id="C_TEST",
        sandbox_executor_url="unix:///run/akesis-exec/exec.sock",
        sandbox_executor_request_key="q" * 40,
        sandbox_executor_response_key="r" * 40,
    )
    assert prod_settings.environment == "production"
    assert prod_settings.read_api_token == "prod_read_token_distinct_987654"


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
        "read_api_token": "prod_read_token_distinct_987654",
        "slack_approver_user_ids": ["U_TEST"],
        "slack_workspace_id": "T_TEST",
        "slack_channel_id": "C_TEST",
        "sandbox_executor_url": "unix:///run/akesis-exec/exec.sock",
        "sandbox_executor_request_key": "q" * 40,
        "sandbox_executor_response_key": "r" * 40,
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


def test_production_config_rejects_read_token_equal_to_app_key() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(**_valid_production(read_api_token="synthetic-app-key-material"))  # type: ignore[arg-type]
    assert "READ_API_TOKEN must be distinct" in str(exc_info.value)


def test_personal_access_token_setting_no_longer_exists() -> None:
    """An old GITHUB_TOKEN in the environment is ignored; nothing can read it."""
    assert "github_token" not in Settings.model_fields
