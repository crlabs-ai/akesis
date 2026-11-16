"""Unit tests for worker payload reconstruction, heartbeat and readiness."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.packages.database.models import WebhookEventModel
from src.packages.shared.models import WorkflowRunConclusion
from src.packages.shared.webhook_worker import (
    STATE_DATABASE_UNAVAILABLE,
    STATE_OK,
    HeartbeatFile,
    _conclusion,
    build_verification_payload,
    check_heartbeat,
)

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


def _row(**overrides: object) -> WebhookEventModel:
    values: dict[str, object] = {
        "delivery_id": "d-1",
        "payload_sha256": "a" * 64,
        "event_type": "workflow_run",
        "action": "completed",
        "installation_id": 404,
        "event_repository_id": 101,
        "event_repository_name": "base/repo",
        "run_id": 9001,
        "run_attempt": 2,
        "source_repository_id": 202,
        "source_repository_name": "fork/repo",
        "source_sha": "b" * 40,
        "source_branch": "feature",
        "workflow_name": "CI",
        "run_url": "https://example.invalid/run/1",
        "conclusion": "failure",
    }
    values.update(overrides)
    return WebhookEventModel(**values)


def test_payload_is_rebuilt_from_persisted_identity_only() -> None:
    payload = build_verification_payload(_row())
    assert payload is not None
    assert payload["installation"] == {"id": 404}
    assert payload["repository"] == {
        "id": 101,
        "full_name": "base/repo",
        "name": "repo",
        "owner": {"login": "base"},
    }
    run = payload["workflow_run"]
    assert run["id"] == 9001 and run["run_attempt"] == 2
    assert run["head_sha"] == "b" * 40 and run["head_branch"] == "feature"
    assert run["head_repository"] == {"id": 202, "full_name": "fork/repo"}


@pytest.mark.parametrize(
    "field",
    [
        "installation_id",
        "event_repository_id",
        "event_repository_name",
        "run_id",
        "run_attempt",
        "source_repository_id",
        "source_repository_name",
        "source_sha",
        "source_branch",
    ],
)
def test_any_missing_identity_field_yields_no_payload(field: str) -> None:
    # An incomplete identity is rejected, never guessed.
    assert build_verification_payload(_row(**{field: None})) is None


def test_payload_contains_no_signature_header_or_digest_material() -> None:
    payload = build_verification_payload(_row())
    assert payload is not None
    text = json.dumps(payload)
    assert "a" * 64 not in text  # payload digest
    assert "signature" not in text.lower()
    assert "token" not in text.lower()


def test_unknown_conclusion_maps_to_unknown_enum() -> None:
    assert _conclusion("failure") is WorkflowRunConclusion.FAILURE
    assert _conclusion("not-a-conclusion") is WorkflowRunConclusion.UNKNOWN
    assert _conclusion(None) is WorkflowRunConclusion.UNKNOWN


def test_heartbeat_is_written_atomically_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    HeartbeatFile(str(path)).write(STATE_OK)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == STATE_OK
    assert not list(tmp_path.glob("*.tmp"))
    assert check_heartbeat(str(path), max_age_seconds=60) == (True, "ok")


def test_heartbeat_reports_outage_as_not_ready(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    HeartbeatFile(str(path)).write(STATE_DATABASE_UNAVAILABLE)
    healthy, reason = check_heartbeat(str(path), max_age_seconds=60)
    assert not healthy and "database_unavailable" in reason


def test_stale_heartbeat_fails_readiness(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    stamp = NOW - timedelta(seconds=300)
    path.write_text(json.dumps({"state": STATE_OK, "updated_at": stamp.isoformat()}))
    healthy, reason = check_heartbeat(str(path), max_age_seconds=60, now=NOW)
    assert not healthy and "stale" in reason


@pytest.mark.parametrize("content", [None, "not json", '{"state": "ok"}'])
def test_missing_or_unreadable_heartbeat_fails_readiness(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / "heartbeat.json"
    if content is not None:
        path.write_text(content)
    healthy, _ = check_heartbeat(str(path), max_age_seconds=60, now=NOW)
    assert not healthy


def test_heartbeat_write_failure_does_not_raise(tmp_path: Path) -> None:
    # A heartbeat that cannot be written must never stop verification.
    HeartbeatFile(str(tmp_path / "missing-dir" / "heartbeat.json")).write(STATE_OK)


def test_heartbeat_does_not_follow_a_planted_symlink(tmp_path: Path) -> None:
    """Regression: the predictable temp name must not let a local user redirect the write."""
    import os as _os

    victim = tmp_path / "victim.env"
    victim.write_text("SECRET=keep-me\n", encoding="utf-8")
    heartbeat = tmp_path / "heartbeat.json"
    planted = heartbeat.with_name(heartbeat.name + f".{_os.getpid()}.tmp")
    _os.symlink(victim, planted)

    HeartbeatFile(str(heartbeat)).write(STATE_OK)

    assert victim.read_text(encoding="utf-8") == "SECRET=keep-me\n"
    assert check_heartbeat(str(heartbeat), max_age_seconds=60)[0]


def test_heartbeat_is_world_readable_but_holds_no_secrets(tmp_path: Path) -> None:
    import stat as _stat

    heartbeat = tmp_path / "heartbeat.json"
    HeartbeatFile(str(heartbeat)).write(STATE_OK)
    mode = _stat.S_IMODE(heartbeat.stat().st_mode)
    assert mode == 0o644
    assert set(json.loads(heartbeat.read_text(encoding="utf-8"))) == {"state", "updated_at", "pid"}


def test_verification_deadline_is_bounded_below_the_stale_claim_window() -> None:
    """Regression: a live verification must always finish before its claim can be reclaimed."""
    from src.packages.database.webhook_events import STALE_VERIFICATION_WINDOW
    from src.packages.shared.config import Settings

    field = Settings.model_fields["webhook_verification_deadline_seconds"]
    maximum = next(m.le for m in field.metadata if hasattr(m, "le"))
    assert maximum < STALE_VERIFICATION_WINDOW.total_seconds()
    assert field.default < STALE_VERIFICATION_WINDOW.total_seconds()
