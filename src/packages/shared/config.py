from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Akesis application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development", description="Runtime environment")
    log_level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")

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
    github_token: str = Field(
        default="",
        description="GitHub token for API access and log retrieval",
    )
    github_api_url: str = Field(
        default="https://api.github.com",
        description="Base URL for the GitHub REST API",
    )

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
        default=30.0,
        description="HTTP request timeout for LLM provider requests in seconds",
    )

    # Codebase Context Retrieval Limits
    repo_checkout_base_dir: str = Field(
        default="/tmp/akesis/repos",
        description="Directory where git repositories are checked out",
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
    approval_ttl_hours: int = Field(
        default=24,
        description="Hours before a pending human approval request expires",
    )


# Singleton settings instance
settings = Settings()
