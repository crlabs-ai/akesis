import json
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Akesis application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development", description="Runtime environment")
    # Phase 1.14c: which service loads these settings. Production checks the credentials each
    # service actually needs; the API may not hold validation-executor keys at all.
    service_role: Literal["api", "worker"] = Field(default="api")
    log_level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")

    # User authentication (Phase 2, ADR-0012): GitHub App user authorization + server sessions.
    # The shared read token was retired; tenant data is read only through a signed-in session.
    github_app_client_id: str = Field(default="", description="GitHub App OAuth client ID")
    github_app_client_secret: str = Field(default="", repr=False)
    auth_redirect_uri: str = Field(
        default="http://localhost:5173/auth/callback",
        description="Callback URL registered on the GitHub App (the web app's /auth/callback)",
    )
    auth_session_ttl_hours: int = Field(default=12, ge=1, le=720)
    auth_cookie_secure: bool = Field(
        default=True,
        description="Send session cookies over HTTPS only (disable only for local http)",
    )
    auth_token_encryption_key: str = Field(
        default="",
        repr=False,
        description="Fernet key that encrypts GitHub user tokens at rest (server-side only)",
    )

    # CORS Configuration
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        description="Allowed CORS origin URLs",
    )
    cors_allow_credentials: bool = Field(
        default=False,
        description="Whether to allow credentials in CORS requests",
    )
    cors_allowed_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        description="Allowed HTTP methods for CORS requests",
    )
    cors_allowed_headers: list[str] = Field(
        default_factory=lambda: [
            "Content-Type",
            "X-Akesis-CSRF",
            "X-Hub-Signature-256",
            "X-GitHub-Event",
            "X-Slack-Signature",
            "X-Slack-Request-Timestamp",
            "X-Request-ID",
            "Authorization",
        ],
        description="Allowed HTTP headers for CORS requests",
    )

    # Trusted Host Protection
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver", "test"],
        description="Allowed hosts for TrustedHostMiddleware",
    )

    # Database Configuration
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgrespassword@localhost:5436/akesis",
        description="Async connection URL for PostgreSQL database",
    )

    # GitHub Integration
    github_webhook_secret: str = Field(
        default="test_webhook_secret",
        description="HMAC secret used to verify incoming GitHub webhooks",
    )
    github_api_url: str = Field(
        default="https://api.github.com",
        description="Base URL for the GitHub REST API",
    )
    github_app_id: int = Field(default=0, repr=False)
    github_app_private_key: str = Field(default="", repr=False)
    # Total verification attempts per delivery, including the initial attempt.
    webhook_retry_max_attempts: int = Field(default=5, ge=1, le=100)
    # Computed exponential backoff: base * 2^(attempt-1), jittered, capped at max_delay.
    webhook_retry_base_delay_seconds: int = Field(default=60, ge=1, le=3600)
    webhook_retry_max_delay_seconds: int = Field(default=900, ge=1, le=86400)
    # Sanity bound for provider-directed delays (Retry-After / rate-limit reset). Larger values
    # are treated as invalid and the computed backoff applies instead.
    webhook_retry_provider_delay_ceiling_seconds: int = Field(default=86400, ge=60, le=604800)
    # Total time allowed for one provenance verification (all GitHub requests together). Must stay
    # below the 10-minute stale-claim window, so a live verification is never reclaimed.
    webhook_verification_deadline_seconds: int = Field(default=240, ge=30, le=540)
    # Retry worker (separate process: python -m src.apps.worker)
    worker_poll_seconds: float = Field(default=15.0, ge=1.0, le=3600.0)
    worker_batch_size: int = Field(default=20, ge=1, le=500)
    worker_db_backoff_max_seconds: float = Field(default=60.0, ge=1.0, le=900.0)
    worker_heartbeat_path: str = Field(default="/run/akesis/worker-heartbeat.json")
    worker_heartbeat_max_age_seconds: int = Field(default=300, ge=5, le=3600)

    # Gemini AI Integration
    gemini_api_key: str = Field(
        default="",
        description="API key for Google Gemini model access",
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model identifier for diagnostic and fix analysis",
    )
    gemini_api_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        description="Base REST URL for Gemini API",
    )
    gemini_timeout_seconds: float = Field(
        default=60.0,
        description="HTTP request timeout for LLM provider requests in seconds",
    )

    # Codebase Context Retrieval Limits
    snapshot_base_dir: str = Field(
        default="/tmp/akesis/snapshots",
        description="Private, owner-only directory for immutable source snapshots",
    )
    snapshot_max_files: int = Field(
        default=2000,
        ge=1,
        description="Maximum number of blob entries materialized into one snapshot",
    )
    snapshot_max_file_bytes: int = Field(
        default=1_000_000,
        ge=1,
        description="Maximum size of one materialized file in bytes",
    )
    snapshot_max_total_bytes: int = Field(
        default=20_000_000,
        ge=1,
        description="Maximum total size of one materialized snapshot in bytes",
    )
    snapshot_ttl_seconds: int = Field(
        default=900,
        ge=1,
        description="Lifetime of a prepared snapshot handle before it must be re-prepared",
    )
    # Workflow log retrieval (Phase 1.10). The archive is untrusted input: every limit fails closed.
    log_retrieval_max_attempts: int = Field(default=5, ge=1, le=50)
    log_retrieval_deadline_seconds: float = Field(default=120.0, gt=0, le=540)
    log_retrieval_lease_seconds: int = Field(default=600, ge=60, le=3600)
    log_archive_max_compressed_bytes: int = Field(default=20_000_000, ge=1)
    log_archive_max_entries: int = Field(default=500, ge=1)
    log_archive_max_uncompressed_bytes: int = Field(default=100_000_000, ge=1)
    log_archive_max_file_bytes: int = Field(default=20_000_000, ge=1)
    log_archive_max_compression_ratio: int = Field(default=100, ge=1)
    log_parse_max_chars: int = Field(default=2_000_000, ge=1000)
    # Hosts GitHub redirects log downloads to. A redirect anywhere else is refused.
    log_download_hosts: list[str] = Field(
        default_factory=lambda: ["actions.githubusercontent.com", "blob.core.windows.net"]
    )
    max_context_window_lines: int = Field(
        default=40,
        description="Maximum lines of source code extracted around target line",
    )
    max_file_size_bytes: int = Field(
        default=500_000,
        description="Maximum file size in bytes to inspect (default 500KB)",
    )
    max_evidence_files: int = Field(
        default=3,
        description="Maximum number of relevant source files to extract per incident",
    )
    max_total_source_chars: int = Field(
        default=8_000,
        description="Hard character budget for all combined source evidence snippets",
    )

    # Fix Proposal Engine Limits
    max_fix_target_files: int = Field(
        default=2,
        description="Maximum number of target files a single fix proposal may modify",
    )
    max_patch_lines: int = Field(
        default=100,
        description="Maximum total lines allowed in a unified diff patch",
    )
    max_patch_chars: int = Field(
        default=4_000,
        description="Maximum total characters allowed in a unified diff patch",
    )
    min_fix_confidence_threshold: float = Field(
        default=0.60,
        description="Minimum diagnostic confidence required to attempt fix generation",
    )

    # Docker Sandbox Validation Settings
    sandbox_image: str = Field(
        default="akesis-validator:v1",
        description="Dedicated Docker image containing pre-installed validation tools",
    )
    sandbox_timeout_seconds: float = Field(
        default=30.0,
        description="Maximum execution timeout for validation container in seconds",
    )
    sandbox_memory_limit: str = Field(
        default="512m",
        description="Memory limit for validation container",
    )
    sandbox_cpu_limit: float = Field(
        default=1.0,
        description="CPU core quota for validation container",
    )
    sandbox_max_output_chars: int = Field(
        default=4_000,
        description="Maximum character limit for captured stdout and stderr",
    )
    sandbox_base_dir: str = Field(
        default="/tmp/akesis/sandbox",
        description="Base directory on host for ephemeral sandbox workspaces",
    )
    # Phase 1.14a: remote validation executor. Empty keeps the local DockerSandboxRunner. When set,
    # validation goes only to the executor and never falls back to local Docker.
    sandbox_executor_url: str = Field(
        default="",
        description="unix:///absolute/path/to/executor.sock for the validation executor",
    )
    sandbox_executor_request_key: str = Field(default="", repr=False)
    sandbox_executor_response_key: str = Field(default="", repr=False)
    sandbox_executor_timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    # Phase 1.14c: approval execution in the remediation worker. The lease must exceed the
    # deadline, so a live remediation is never reclaimed.
    remediation_max_attempts: int = Field(default=3, ge=1, le=20)
    remediation_deadline_seconds: float = Field(default=480.0, gt=0, le=3000)
    remediation_lease_seconds: int = Field(default=900, ge=60, le=3600)

    # Human-in-the-Loop & Slack Integration Settings
    slack_webhook_url: str = Field(
        default="",
        description="Slack Incoming Webhook URL for posting approval cards",
    )
    slack_signing_secret: str = Field(
        default="test_slack_signing_secret",
        description="Signing secret to verify Slack interaction webhook requests",
    )
    slack_channel_id: str = Field(
        default="",
        description="Default Slack channel for approval cards",
    )
    slack_approver_user_ids: list[str] = Field(default_factory=list)
    slack_workspace_id: str = Field(default="")
    approval_ttl_hours: int = Field(
        default=24,
        description="Hours before a pending human approval request expires",
    )

    @field_validator(
        "cors_allowed_origins",
        "cors_allowed_methods",
        "cors_allowed_headers",
        "trusted_hosts",
        "slack_approver_user_ids",
        "log_download_hosts",
        mode="before",
    )
    @classmethod
    def _parse_list_from_str(cls, value: Any) -> list[str] | Any:
        """Parses comma-separated strings or JSON arrays into list[str]."""
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed]
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _validate_sandbox_executor(self) -> "Settings":
        """Remote validation requires a Unix socket and two distinct, long signing keys."""
        if not self.sandbox_executor_url:
            return self
        path = self.sandbox_executor_url.removeprefix("unix://")
        if not self.sandbox_executor_url.startswith("unix:///") or "\0" in path:
            raise ValueError("SANDBOX_EXECUTOR_URL must be unix:///absolute/path")
        request_key = self.sandbox_executor_request_key
        response_key = self.sandbox_executor_response_key
        if len(request_key) < 32 or len(response_key) < 32:
            raise ValueError("Sandbox executor request and response keys need 32+ characters")
        if request_key == response_key:
            raise ValueError("Sandbox executor request and response keys must differ")
        return self

    @model_validator(mode="after")
    def _validate_production_configuration(self) -> "Settings":
        """Fail-fast production checks, per service role (Phase 1.14c credential inventory).

        - Both: DATABASE_URL and the GitHub App. The API verifies webhook provenance with it; the
          worker uses it for verification retries, log retrieval, snapshots and mutation.
        - API: the webhook secret, the Slack signing secret and approver policy, CORS, trusted
          hosts and user sign-in (GitHub App OAuth client, token encryption key, secure cookies).
          It must not hold validation-executor configuration.
        - Worker: the validation executor (it is the executor's only caller) and the approver
          allowlist (each approval is re-authorized at execution time). It must not hold the
          webhook secret, the Slack signing secret or sign-in secrets.
        - GEMINI_API_KEY and SLACK_WEBHOOK_URL are used only by diagnosis and approval-card
          posting (`process_failure`), which has no caller until the condition-6 handoff. They
          are not required by either role; when set, placeholders are still rejected.
        """
        is_prod = self.environment.lower() in ("production", "prod")
        if not is_prod:
            return self

        errors: list[str] = []
        api = self.service_role == "api"

        # Database validation
        if not self.database_url:
            errors.append("DATABASE_URL is required in production.")
        elif "postgrespassword" in self.database_url or "localhost:5436" in self.database_url:
            errors.append(
                "DATABASE_URL contains default/development credentials or localhost address."
            )

        if self.github_app_id <= 0 or not self.github_app_private_key.strip():
            errors.append("GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are required in production.")

        if self.gemini_api_key == "your_gemini_api_key_here":
            errors.append("GEMINI_API_KEY is an insecure placeholder.")
        insecure_slack_webhooks = {
            "https://hooks.slack.com/services/...",
            "your_slack_webhook_url_here",
            "placeholder",
        }
        if self.slack_webhook_url in insecure_slack_webhooks:
            errors.append("SLACK_WEBHOOK_URL is an insecure placeholder.")

        if not self.slack_approver_user_ids or any(
            not user.strip() for user in self.slack_approver_user_ids
        ):
            errors.append("SLACK_APPROVER_USER_IDS is required in production.")

        if api:
            errors.extend(self._api_production_errors())
        else:
            if not self.sandbox_executor_url:
                # Phase 1.14b/c: validation runs only in the executor, reached only by the worker.
                errors.append(
                    "SANDBOX_EXECUTOR_URL is required in production for the worker: validation "
                    "must run in the validation executor, never through local Docker."
                )
            # The worker verifies no inbound webhook or Slack request and serves no read API, so
            # it must not hold those secrets (set = non-empty and not the field default).
            api_only = [
                name.upper()
                for name in (
                    "github_webhook_secret",
                    "slack_signing_secret",
                    "github_app_client_secret",
                    "auth_token_encryption_key",
                )
                if getattr(self, name) not in ("", type(self).model_fields[name].default)
            ]
            if api_only:
                errors.append(
                    "API-only credentials must not be set for the worker: " + ", ".join(api_only)
                )

        if errors:
            raise ValueError(
                "Production configuration validation failed with "
                f"{len(errors)} error(s):\n- " + "\n- ".join(errors)
            )

        return self

    def _api_production_errors(self) -> list[str]:
        errors: list[str] = []
        insecure_webhook_secrets = {
            "test_webhook_secret",
            "your_github_webhook_secret_here",
            "secret",
            "placeholder",
        }
        if not self.github_webhook_secret or self.github_webhook_secret in insecure_webhook_secrets:
            errors.append("GITHUB_WEBHOOK_SECRET is missing or using an insecure test placeholder.")

        insecure_slack_secrets = {
            "test_slack_signing_secret",
            "your_slack_signing_secret_here",
            "placeholder",
        }
        if not self.slack_signing_secret or self.slack_signing_secret in insecure_slack_secrets:
            errors.append("SLACK_SIGNING_SECRET is missing or using an insecure placeholder.")
        if not self.slack_workspace_id.strip():
            errors.append("SLACK_WORKSPACE_ID is required in production.")
        if not self.slack_channel_id.strip():
            errors.append("SLACK_CHANNEL_ID is required in production.")

        # Phase 1.14c: the API never calls the executor, so it must not even hold its keys.
        if (
            self.sandbox_executor_url
            or self.sandbox_executor_request_key
            or self.sandbox_executor_response_key
        ):
            errors.append(
                "SANDBOX_EXECUTOR_* must not be set for the API: only the remediation worker "
                "calls the validation executor."
            )

        if not self.cors_allowed_origins:
            errors.append(
                "CORS_ALLOWED_ORIGINS must be configured with explicit origins in production."
            )
        elif any(origin == "*" or "*" in origin for origin in self.cors_allowed_origins):
            errors.append("CORS_ALLOWED_ORIGINS must not contain wildcard '*' in production.")

        if not self.trusted_hosts:
            errors.append("TRUSTED_HOSTS must be explicitly configured in production.")
        elif any(host == "*" or "*" in host for host in self.trusted_hosts):
            errors.append("TRUSTED_HOSTS must not contain wildcard '*' in production.")

        errors.extend(self._auth_production_errors())
        return errors

    def _auth_production_errors(self) -> list[str]:
        """Sign-in must be fully configured, over HTTPS, with a valid encryption key."""
        from urllib.parse import urlsplit

        from cryptography.fernet import Fernet

        errors: list[str] = []
        if not self.github_app_client_id.strip() or not self.github_app_client_secret.strip():
            errors.append(
                "GITHUB_APP_CLIENT_ID and GITHUB_APP_CLIENT_SECRET are required in production."
            )
        redirect = urlsplit(self.auth_redirect_uri)
        if redirect.scheme != "https" or not redirect.netloc or redirect.path != "/auth/callback":
            errors.append("AUTH_REDIRECT_URI must be https://<host>/auth/callback in production.")
        if not self.auth_cookie_secure:
            errors.append("AUTH_COOKIE_SECURE must be true in production.")
        other_secrets = {
            self.github_webhook_secret,
            self.github_app_private_key,
            self.gemini_api_key,
            self.slack_signing_secret,
        }
        if ({self.github_app_client_secret, self.auth_token_encryption_key} & other_secrets) - {""}:
            errors.append("Sign-in secrets must be distinct from GitHub, Gemini and Slack secrets.")
        try:
            Fernet(self.auth_token_encryption_key.encode())
        except Exception:
            errors.append("AUTH_TOKEN_ENCRYPTION_KEY must be a valid Fernet key in production.")
        return errors


# Singleton settings instance
settings = Settings()
