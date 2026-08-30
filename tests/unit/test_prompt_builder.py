from src.packages.shared.models import (
    CodeEvidence,
    EvidencePackage,
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
    assert "NO PATCH GENERATION" in system_inst


def test_prompt_builder_user_prompt_with_code_evidence() -> None:
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

    evidence_pkg = EvidencePackage(
        incident_id="inc_test_123",
        commit_sha="abcdef123456",
        failure_context=context,
        code_evidences=[
            CodeEvidence(
                path="tests/test_calc.py",
                start_line=1,
                end_line=15,
                target_line=10,
                content="  10 > | assert divide(10, 0) == 0",
                total_file_lines=20,
                language="python",
            )
        ],
        retrieval_status="success",
    )

    prompt = DiagnosticPromptBuilder.build_user_prompt(context, evidence_package=evidence_pkg)
    assert "Incident ID: inc_test_123" in prompt
    assert "tests/test_calc.py" in prompt
    assert "REPOSITORY SOURCE CODE EVIDENCE (UNTRUSTED DATA)" in prompt
    assert "assert divide(10, 0) == 0" in prompt
