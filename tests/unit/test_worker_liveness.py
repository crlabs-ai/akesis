"""Liveness signalling for the worker: sd_notify pings and the readiness window."""

import socket
import uuid
from pathlib import Path

import pytest

from src.packages.shared import systemd_notify
from src.packages.shared.config import Settings


def test_notify_is_a_no_op_outside_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert systemd_notify.notify("WATCHDOG=1") is False


def test_notify_delivers_a_datagram_to_the_notify_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(path))
        assert systemd_notify.notify("WATCHDOG=1") is True
        assert server.recv(64) == b"WATCHDOG=1"
    finally:
        server.close()


def test_notify_supports_the_abstract_socket_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    name = f"akesis-test-{uuid.uuid4().hex}"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind("\0" + name)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", "@" + name)
        assert systemd_notify.notify("WATCHDOG=1") is True
        assert server.recv(64) == b"WATCHDOG=1"
    finally:
        server.close()


def test_notify_to_a_missing_socket_fails_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "absent.sock"))
    assert systemd_notify.notify("WATCHDOG=1") is False


def test_heartbeat_window_exceeds_the_verification_deadline() -> None:
    """A healthy worker may spend up to the deadline inside one verification without going stale."""
    fields = Settings.model_fields
    deadline = fields["webhook_verification_deadline_seconds"].default
    window = fields["worker_heartbeat_max_age_seconds"].default
    assert window > deadline + 30


def test_worker_has_no_timer_driven_liveness_that_could_mask_a_hang() -> None:
    """Pings and heartbeats are written only at progress points, never from a background timer."""
    import src.packages.shared.webhook_worker as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("create_task(", "call_later(", "call_periodically", "ensure_future("):
        assert forbidden not in source, f"timer-driven liveness found: {forbidden}"
