import io
import json

import pytest
import structlog

from src.packages.shared.logging import get_logger


def test_structured_logging_correlation_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.StringIO()

    # Configure logging with custom stream
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(output),
        cache_logger_on_first_use=False,
    )

    logger = get_logger("akesis.test")
    logger.info(
        "pipeline_started",
        incident_id="inc_12345",
        pipeline_id="pipe_12345",
        run_id=33539502900,
        repo="crlabs-ai/akesis",
        commit_sha="abcdef1234567890abcdef1234567890abcdef12",
    )

    log_line = output.getvalue().strip()
    data = json.loads(log_line)

    assert data["event"] == "pipeline_started"
    assert data["incident_id"] == "inc_12345"
    assert data["pipeline_id"] == "pipe_12345"
    assert data["run_id"] == 33539502900
    assert data["repo"] == "crlabs-ai/akesis"
    assert data["commit_sha"] == "abcdef1234567890abcdef1234567890abcdef12"
    assert data["level"] == "info"
    assert "timestamp" in data
