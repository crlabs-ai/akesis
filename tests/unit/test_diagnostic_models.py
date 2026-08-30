import pytest
from pydantic import ValidationError

from src.packages.shared.models import (
    DiagnosisProposal,
    EvidenceItem,
    FailureCategory,
    RemediationDirection,
)


def test_valid_diagnosis_proposal() -> None:
    proposal = DiagnosisProposal(
        category=FailureCategory.LINT,
        root_cause="Unused import 'os' in utils.py",
        evidence=[
            EvidenceItem(
                source="log_snippet",
                observation="F401 'os' imported but unused",
                file_path="src/utils.py",
                line_number=5,
            )
        ],
        target_file="src/utils.py",
        target_line=5,
        remediation_direction=RemediationDirection(
            summary="Remove unused import",
            suggested_action="Delete 'import os' line",
            risk_assessment="Zero risk",
        ),
        is_fixable=True,
        confidence_score=0.98,
        evidence_sufficiency="sufficient",
        reasoning="Linter explicitly flagged unused import at line 5.",
    )

    assert proposal.category == FailureCategory.LINT
    assert proposal.confidence_score == 0.98
    assert proposal.is_fixable is True


def test_invalid_confidence_score_rejected() -> None:
    with pytest.raises(ValidationError):
        DiagnosisProposal(
            category=FailureCategory.LINT,
            root_cause="Test",
            evidence=[EvidenceItem(source="log", observation="test")],
            remediation_direction=RemediationDirection(
                summary="a", suggested_action="b", risk_assessment="c"
            ),
            is_fixable=True,
            confidence_score=1.5,  # Invalid: must be <= 1.0
            evidence_sufficiency="sufficient",
            reasoning="test",
        )


def test_empty_evidence_rejected() -> None:
    with pytest.raises(ValidationError):
        DiagnosisProposal(
            category=FailureCategory.LINT,
            root_cause="Test",
            evidence=[],  # Invalid: min_length=1
            remediation_direction=RemediationDirection(
                summary="a", suggested_action="b", risk_assessment="c"
            ),
            is_fixable=True,
            confidence_score=0.5,
            evidence_sufficiency="sufficient",
            reasoning="test",
        )
