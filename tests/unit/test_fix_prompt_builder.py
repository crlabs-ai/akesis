from src.packages.shared.fix_prompt_builder import FixPromptBuilder
from src.packages.shared.models import (
    CodeEvidence,
    DiagnosisProposal,
    DiagnosticResult,
    EvidenceItem,
    EvidencePackage,
    FailureCategory,
    FailureContext,
    FailureSignal,
    RemediationDirection,
    WorkflowRunConclusion,
)


def test_fix_prompt_builder_system_instruction() -> None:
    sys_inst = FixPromptBuilder.build_system_instruction()
    assert "EVIDENCE GROUNDING" in sys_inst
    assert "UNTRUSTED DATA BOUNDARY" in sys_inst
    assert "CANONICAL UNIFIED DIFF" in sys_inst
    assert "BUDGET LIMITS" in sys_inst


def test_fix_prompt_builder_user_prompt() -> None:
    context = FailureContext(
        incident_id="inc_prompt_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=202,
        workflow_name="CI",
        commit_sha="aabbccddeeff",
        branch="feat/test",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.LINT,
            error_type="LintViolation(F401)",
            message="unused import",
            target_file="src/utils.py",
            target_line=5,
            extracted_snippet="F401 unused import os",
        ),
        raw_log_excerpt="F401 unused import os",
    )

    diag_result = DiagnosticResult(
        incident_id="inc_prompt_01",
        proposal=DiagnosisProposal(
            category=FailureCategory.LINT,
            root_cause="Unused import os",
            evidence=[EvidenceItem(source="log", observation="F401 os")],
            target_file="src/utils.py",
            target_line=5,
            remediation_direction=RemediationDirection(
                summary="Remove import",
                suggested_action="Delete import os",
                risk_assessment="Zero risk",
            ),
            is_fixable=True,
            confidence_score=0.95,
            evidence_sufficiency="sufficient",
            reasoning="Direct linter message",
        ),
        human_review_required=True,
        model_name="gemini-1.5-flash",
        execution_time_ms=120.0,
    )

    evidence_pkg = EvidencePackage(
        incident_id="inc_prompt_01",
        commit_sha="aabbccddeeff",
        failure_context=context,
        code_evidences=[
            CodeEvidence(
                path="src/utils.py",
                start_line=1,
                end_line=10,
                target_line=5,
                content="5 > | import os",
                total_file_lines=10,
                language="python",
            )
        ],
        retrieval_status="success",
    )

    prompt = FixPromptBuilder.build_user_prompt(
        context=context,
        diagnostic_result=diag_result,
        evidence_package=evidence_pkg,
    )

    assert "Incident ID: inc_prompt_01" in prompt
    assert "Unused import os" in prompt
    assert "src/utils.py" in prompt
    assert "import os" in prompt
    assert "REPOSITORY SOURCE CODE EVIDENCE (UNTRUSTED DATA)" in prompt
