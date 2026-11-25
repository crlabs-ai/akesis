"""Security regression tests: every remediation entry point is gated before any side effect.

These tests pin the structure that keeps ADR-0010's hold unconditional: gates run first, the
webhook path never schedules remediation, and no test-only switch can change the hold.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.packages.shared.mutation_service as mutation_module
import src.packages.shared.remediation_orchestrator as orchestrator_module
from src.packages.shared.provenance import UnverifiedProvenanceError, require_verified_provenance
from src.packages.shared.remediation_orchestrator import RemediationOrchestrator

REPO = Path(orchestrator_module.__file__).resolve().parents[3]


def test_hold_is_unconditional_regardless_of_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.packages.shared.config import settings

    for environment in ("development", "test", "production"):
        monkeypatch.setattr(settings, "environment", environment)
        with pytest.raises(UnverifiedProvenanceError):
            require_verified_provenance()


@pytest.mark.asyncio
async def test_process_failure_refuses_before_any_database_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = AsyncMock(side_effect=AssertionError("database read before the hold"))
    monkeypatch.setattr(orchestrator_module, "verify_context_provenance", verify)
    orchestrator = RemediationOrchestrator()
    with pytest.raises(UnverifiedProvenanceError):
        await orchestrator.process_failure(object())  # type: ignore[arg-type]
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_approval_refuses_before_any_database_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = AsyncMock(side_effect=AssertionError("database read before the hold"))
    monkeypatch.setattr(orchestrator_module, "verify_approval_provenance", verify)
    orchestrator = RemediationOrchestrator()
    with pytest.raises(UnverifiedProvenanceError):
        await orchestrator.resume_approval("apr_synthetic")
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_mutation_pull_request_refuses_before_preflight_or_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = AsyncMock(side_effect=AssertionError("database read before the hold"))
    monkeypatch.setattr(mutation_module, "verify_context_provenance", verify)
    service = mutation_module.GitMutationService()
    with pytest.raises(UnverifiedProvenanceError):
        await service.create_pull_request(None, None, None, None)  # type: ignore[arg-type]
    verify.assert_not_awaited()


def _call_sites(name: str) -> list[str]:
    hits: list[str] = []
    for path in sorted((REPO / "src").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if f"{name}(" in line and not line.lstrip().startswith(("def ", "async def ", "#")):
                hits.append(f"{path.relative_to(REPO)}:{number}")
    return hits


def test_entry_point_census_has_no_unexpected_call_site() -> None:
    """Public remediation entry points are called only where the census says they may be."""
    assert _call_sites("process_failure") == []
    # Phase 1.14c: only the remediation worker resumes approvals; the API never does.
    worker = "src/packages/shared/webhook_worker.py"
    assert _call_sites("resume_approval") == [
        f"{worker}:" + _line_of(worker, "await self._orchestrator.resume_approval(approval_id)")
    ]
    # The unchecked bodies are reached only from their gated public methods.
    assert len(_call_sites("_process_failure_unchecked")) == 1
    assert len(_call_sites("_resume_approval_unchecked")) == 1
    assert len(_call_sites("_create_pull_request_unchecked")) == 1


def _line_of(relative: str, needle: str) -> str:
    for number, line in enumerate((REPO / relative).read_text().splitlines(), 1):
        if needle in line:
            return str(number)
    raise AssertionError(f"{needle!r} not found in {relative}")


def test_webhook_route_never_schedules_remediation() -> None:
    lines = (REPO / "src/apps/api/routes.py").read_text(encoding="utf-8").splitlines()
    assert not any("process_failure" in line for line in lines)
    # Phase 1.14c: the API schedules no background work and never reaches the orchestrator.
    assert not any("add_task(" in line for line in lines)
    assert not any("resume_approval" in line or "RemediationOrchestrator" in line for line in lines)


def test_no_environment_switch_can_reach_the_remediation_or_provenance_modules() -> None:
    guarded = [
        "src/packages/shared/provenance.py",
        "src/packages/shared/remediation_orchestrator.py",
        "src/packages/shared/mutation_service.py",
        "src/packages/shared/webhook_admission.py",
        "src/packages/shared/persisted_provenance.py",
    ]
    for relative in guarded:
        text = (REPO / relative).read_text(encoding="utf-8")
        for forbidden in ("settings.environment", "TEST_MODE", "os.environ", "os.getenv"):
            assert forbidden not in text, f"{relative} reads {forbidden}"


def test_sandbox_runs_without_network_privileges_or_unbounded_processes() -> None:
    from src.packages.sdk import sandbox_runner

    text = Path(sandbox_runner.__file__).read_text(encoding="utf-8")
    for required in (
        '"--network",\n            "none"',
        '"ALL"',
        "no-new-privileges",
        "--pids-limit",
    ):
        assert required in text, required
