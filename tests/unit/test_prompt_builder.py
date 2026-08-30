from src.packages.shared.models import (
    FailureCategory,
    FailureContext,
    FailureSignal,
    WorkflowRunConclusion,
)
from src.packages.shared.prompt_builder import DiagnosticPromptBuilder


def test_prompt_builder_system_instruction() -> None:
    system_inst = DiagnosticPromptBuilder.build_system_instruction()
    assert "EVIDENCE FIRST" in system_inst
    assert "UNTRUSTED DATA BOUNDARY" in system_inst
    assert "NO COMMAND EXECUTION" in system_inst
    assert "confidence_score" in system_inst


def test_prompt_builder_user_prompt() -> None:
    context = FailureContext(
        incident_id="inc_test_123",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=12345,
        workflow_name="CI / Tests",
        commit_sha="abcdef123456",
        branch="main",
        run_url="https://github.com/crlabs-ai/akesis/actions/runs/12345",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="ZeroDivisionError",
            message="division by zero",
            target_file="tests/test_calc.py",
            target_line=10,
            extracted_snippet="ZeroDivisionError: division by zero",
        ),
        raw_log_excerpt="ZeroDivisionError: division by zero",
    )

    prompt = DiagnosticPromptBuilder.build_user_prompt(context)
    assert "Incident ID: inc_test_123" in prompt
    assert "crlabs-ai/akesis" in prompt
    assert "tests/test_calc.py" in prompt
    assert "ZeroDivisionError" in prompt
