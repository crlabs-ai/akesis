import os

from src.packages.shared.log_parser import filter_and_extract_signal, strip_ansi_codes
from src.packages.shared.models import FailureCategory

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "../fixtures")


def read_fixture(fname: str) -> str:
    with open(os.path.join(FIXTURES_DIR, fname)) as f:
        return f.read()


def test_strip_ansi_codes() -> None:
    ansi_text = "\x1b[31mError:\x1b[0m Process failed"
    assert strip_ansi_codes(ansi_text) == "Error: Process failed"


def test_pytest_failure_extraction() -> None:
    logs = read_fixture("logs_pytest_failure.txt")
    signal = filter_and_extract_signal(logs)

    assert signal.category == FailureCategory.TEST
    assert signal.error_type == "ZeroDivisionError"
    assert "division by zero" in signal.message
    assert signal.target_file == "tests/test_calculator.py"
    assert signal.target_line == 18
    assert len(signal.traceback_frames) >= 1


def test_modulenotfound_failure_extraction() -> None:
    logs = read_fixture("logs_modulenotfound_failure.txt")
    signal = filter_and_extract_signal(logs)

    assert signal.category == FailureCategory.DEPENDENCY
    assert signal.error_type == "ModuleNotFoundError"
    assert "non_existent_package" in signal.message
    assert signal.target_file == "/home/runner/work/akesis/src/apps/api/main.py"
    assert signal.target_line == 4


def test_ruff_lint_failure_extraction() -> None:
    logs = read_fixture("logs_ruff_lint_failure.txt")
    signal = filter_and_extract_signal(logs)

    assert signal.category == FailureCategory.LINT
    assert "LintViolation" in signal.error_type
    assert signal.target_file == "src/packages/shared/utils.py"
    assert signal.target_line == 24


def test_mypy_type_failure_extraction() -> None:
    logs = read_fixture("logs_mypy_type_failure.txt")
    signal = filter_and_extract_signal(logs)

    assert signal.category == FailureCategory.LINT
    assert signal.error_type == "TypeError"
    assert signal.target_file == "src/packages/sdk/client.py"
    assert signal.target_line == 45


def test_pip_dependency_failure_extraction() -> None:
    logs = read_fixture("logs_pip_dependency_failure.txt")
    signal = filter_and_extract_signal(logs)

    assert signal.category == FailureCategory.DEPENDENCY
    assert signal.error_type == "DependencyResolutionError"
    assert "pydantic-core" in signal.message


def test_generic_shell_failure_extraction() -> None:
    logs = read_fixture("logs_generic_shell_failure.txt")
    signal = filter_and_extract_signal(logs)

    assert signal.category == FailureCategory.UNKNOWN
    assert signal.error_type == "GenericCIError"
    assert "FATAL" in signal.message
