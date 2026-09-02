from pathlib import Path

import pytest

from src.packages.shared.models import (
    CodeEvidence,
    EvidencePackage,
    FailureCategory,
    FailureContext,
    FailureSignal,
    WorkflowRunConclusion,
)
from src.packages.shared.patch_validator import PatchValidator


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()
    src_dir = repo_dir / "src"
    src_dir.mkdir()
    (src_dir / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (src_dir / "auth.py").write_text("def login(): pass\n", encoding="utf-8")
    (src_dir / "helper.py").write_text("def helper(): pass\n", encoding="utf-8")
    (src_dir / "extra.py").write_text("def extra(): pass\n", encoding="utf-8")
    return repo_dir


@pytest.fixture
def sample_evidence_package() -> EvidencePackage:
    context = FailureContext(
        incident_id="inc_val_01",
        repository_owner="crlabs-ai",
        repository_name="akesis",
        run_id=101,
        workflow_name="CI",
        commit_sha="112233445566",
        branch="main",
        run_url="https://ci",
        conclusion=WorkflowRunConclusion.FAILURE,
        signal=FailureSignal(
            category=FailureCategory.TEST,
            error_type="ZeroDivisionError",
            message="err",
            target_file="src/calculator.py",
            target_line=1,
            extracted_snippet="err",
        ),
        raw_log_excerpt="err",
    )
    return EvidencePackage(
        incident_id="inc_val_01",
        commit_sha="112233445566",
        failure_context=context,
        code_evidences=[
            CodeEvidence(
                path="src/calculator.py",
                start_line=1,
                end_line=5,
                target_line=1,
                content="1 | def add(a, b): return a + b",
                total_file_lines=5,
                language="python",
            )
        ],
        retrieval_status="success",
    )


def test_valid_single_file_patch(sample_evidence_package: EvidencePackage, temp_repo: Path) -> None:
    validator = PatchValidator()
    diff = (
        "--- a/src/calculator.py\n"
        "+++ b/src/calculator.py\n"
        "@@ -1,1 +1,3 @@\n"
        " def add(a, b):\n"
        "+    if b == 0:\n"
        "+        raise ValueError('Zero denominator')\n"
        "     return a + b"
    )

    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["src/calculator.py"],
        evidence_package=sample_evidence_package,
        repo_root=temp_repo,
    )

    assert result.is_valid is True
    assert len(result.rejection_reasons) == 0
    assert len(result.file_patches) == 1
    assert result.file_patches[0].path == "src/calculator.py"
    assert result.target_files == ["src/calculator.py"]
    assert result.has_dependency_changes is False
    assert result.risk_level == "low"


def test_empty_patch_rejected() -> None:
    validator = PatchValidator()
    result = validator.validate_patch(raw_diff="", claimed_target_files=[])
    assert result.is_valid is False
    assert any("empty" in r for r in result.rejection_reasons)


def test_patch_char_budget_exceeded() -> None:
    validator = PatchValidator(max_patch_chars=50)
    diff = (
        "--- a/src/calculator.py\n"
        "+++ b/src/calculator.py\n"
        "@@ -1,1 +1,1 @@\n"
        "+long content exceeding budget limit...\n"
    )
    result = validator.validate_patch(raw_diff=diff, claimed_target_files=["src/calculator.py"])
    assert result.is_valid is False
    assert any("exceeds maximum" in r for r in result.rejection_reasons)


def test_too_many_target_files_rejected(temp_repo: Path) -> None:
    validator = PatchValidator(max_target_files=2)
    diff = (
        "--- a/src/calculator.py\n+++ b/src/calculator.py\n@@ -1,1 +1,1 @@\n+1\n"
        "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1,1 +1,1 @@\n+2\n"
        "--- a/src/helper.py\n+++ b/src/helper.py\n@@ -1,1 +1,1 @@\n+3\n"
    )
    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["src/calculator.py", "src/auth.py", "src/helper.py"],
        repo_root=temp_repo,
    )
    assert result.is_valid is False
    assert any("modifies 3 files" in r for r in result.rejection_reasons)


def test_malformed_diff_missing_plus_header() -> None:
    validator = PatchValidator()
    diff = "--- a/src/calculator.py\n@@ -1,1 +1,1 @@\n+1\n"
    result = validator.validate_patch(raw_diff=diff, claimed_target_files=["src/calculator.py"])
    assert result.is_valid is False
    assert any("without" in r for r in result.rejection_reasons)


def test_malformed_hunk_header_syntax() -> None:
    validator = PatchValidator()
    diff = "--- a/src/calculator.py\n+++ b/src/calculator.py\n@@ invalid header @@\n+1\n"
    result = validator.validate_patch(raw_diff=diff, claimed_target_files=["src/calculator.py"])
    assert result.is_valid is False
    assert any("Malformed hunk header" in r for r in result.rejection_reasons)


def test_invalid_line_prefix_in_hunk() -> None:
    validator = PatchValidator()
    diff = "--- a/src/calculator.py\n+++ b/src/calculator.py\n@@ -1,1 +1,1 @@\n? invalid prefix\n"
    result = validator.validate_patch(raw_diff=diff, claimed_target_files=["src/calculator.py"])
    assert result.is_valid is False
    assert any("Invalid line prefix" in r for r in result.rejection_reasons)


def test_grounding_verified_in_repo_without_evidence_pkg(temp_repo: Path) -> None:
    validator = PatchValidator()
    diff = (
        "--- a/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@ -1,1 +1,2 @@\n"
        "-def login(): pass\n"
        "+def login(): return True\n"
    )

    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["src/auth.py"],
        evidence_package=None,
        repo_root=temp_repo,
    )

    assert result.is_valid is True
    assert result.target_files == ["src/auth.py"]


def test_unverified_arbitrary_path_rejected(temp_repo: Path) -> None:
    validator = PatchValidator()
    diff = "--- a/src/non_existent.py\n+++ b/src/non_existent.py\n@@ -1,1 +1,1 @@\n+# new file\n"

    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["src/non_existent.py"],
        evidence_package=None,
        repo_root=temp_repo,
    )

    assert result.is_valid is False
    assert any("neither grounded in evidence nor verified" in r for r in result.rejection_reasons)


def test_path_traversal_rejected(temp_repo: Path) -> None:
    validator = PatchValidator()
    diff = "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1,1 +1,1 @@\n+root:x:0:0:::\n"

    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["../../etc/passwd"],
        repo_root=temp_repo,
    )

    assert result.is_valid is False
    assert any("outside root" in r or "traversal" in r for r in result.rejection_reasons)


def test_protected_workflow_file_rejected(temp_repo: Path) -> None:
    validator = PatchValidator()
    diff = (
        "--- a/.github/workflows/ci.yml\n"
        "+++ b/.github/workflows/ci.yml\n"
        "@@ -1,1 +1,1 @@\n"
        "+# bypass ci\n"
    )

    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=[".github/workflows/ci.yml"],
        repo_root=temp_repo,
    )

    assert result.is_valid is False
    assert any("protected security pattern" in r for r in result.rejection_reasons)


def test_dependency_change_flagged_as_high_risk(temp_repo: Path) -> None:
    (temp_repo / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
    validator = PatchValidator()
    diff = (
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        "@@ -1,1 +1,2 @@\n"
        " [project]\n"
        "+dependencies = ['pydantic']\n"
    )

    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["pyproject.toml"],
        repo_root=temp_repo,
    )

    assert result.is_valid is True
    assert result.has_dependency_changes is True
    assert result.risk_level == "high"


def test_oversized_patch_rejected() -> None:
    validator = PatchValidator(max_patch_lines=5)
    diff = (
        "--- a/src/test.py\n"
        "+++ b/src/test.py\n"
        "@@ -1,1 +1,10 @@\n"
        "+1\n+2\n+3\n+4\n+5\n+6\n+7\n+8\n+9\n+10\n"
    )

    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["src/test.py"],
    )

    assert result.is_valid is False
    assert any("exceeds maximum" in r for r in result.rejection_reasons)


def test_test_file_only_patch_rejected(temp_repo: Path) -> None:
    (temp_repo / "tests").mkdir(exist_ok=True)
    (temp_repo / "tests" / "test_calc.py").write_text(
        "def test_add(): assert 1 == 2\n", encoding="utf-8"
    )
    validator = PatchValidator()
    diff = (
        "--- a/tests/test_calc.py\n"
        "+++ b/tests/test_calc.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def test_add(): assert 1 == 2\n"
        "+def test_add(): assert 1 == 1\n"
    )
    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["tests/test_calc.py"],
        repo_root=temp_repo,
    )
    assert result.is_valid is False
    assert any("exclusively modifies test files" in r for r in result.rejection_reasons)


def test_application_source_patch_accepted(temp_repo: Path) -> None:
    (temp_repo / "src").mkdir(exist_ok=True)
    (temp_repo / "src" / "calc.py").write_text(
        "def add(a, b): return a + b + 1\n", encoding="utf-8"
    )
    validator = PatchValidator()
    diff = (
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def add(a, b): return a + b + 1\n"
        "+def add(a, b): return a + b\n"
    )
    result = validator.validate_patch(
        raw_diff=diff,
        claimed_target_files=["src/calc.py"],
        repo_root=temp_repo,
    )
    assert result.is_valid is True
    assert result.target_files == ["src/calc.py"]
