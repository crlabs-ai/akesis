import re

from src.packages.shared.models import FailureCategory, FailureSignal, TracebackFrame

# ANSI escape sequence regex pattern
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~])")

# Python Traceback pattern
TRACEBACK_BLOCK_PATTERN = re.compile(
    r"Traceback \(most recent call last\):(?P<frames>.*?)"
    r"(?P<exception>\w+(?:Error|Exception|Warning|Interrupt)): (?P<msg>.+)",
    re.DOTALL,
)

# Individual frame line pattern: File "path/to/file.py", line 123, in func_name / <module>
FRAME_PATTERN = re.compile(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+)'
    r"(?:,\s*in\s+(?P<func>[^\n]+))?\n(?:\s+(?P<code>.+))?"
)

# Pytest short summary pattern: FAILED tests/test_foo.py::test_bar - ZeroDivisionError: ...
PYTEST_FAILED_PATTERN = re.compile(
    r"FAILED (?P<file>[^:]+)::(?P<test>[^\s-]+)"
    r"(?: - (?:(?P<exc>\w+(?:Error|Exception)):\s*)?(?P<err>.*))?"
)

# Pytest line error reference: tests/test_calculator.py:18: ZeroDivisionError
PYTEST_LINE_REF_PATTERN = re.compile(
    r"(?P<file>[a-zA-Z0-9_/\.-]+\.py):(?P<line>\d+):\s+(?P<exc>\w+(?:Error|Exception))"
)

# Ruff / Flake8 lint pattern: path/to/file.py:12:5: E999 SyntaxError: ...
RUFF_LINT_PATTERN = re.compile(
    r"(?P<file>[a-zA-Z0-9_/\.-]+\.py):(?P<line>\d+):(?P<col>\d+):\s+(?P<code>[A-Z0-9]+)\s+(?P<msg>.+)"
)

# Mypy type error pattern: path/to/file.py:10: error: Incompatible types...
MYPY_PATTERN = re.compile(
    r"(?P<file>[a-zA-Z0-9_/\.-]+\.(?:py|ts|tsx)):(?P<line>\d+):\s+error:\s+(?P<msg>.+)"
)

# Pip / dependency error patterns
PIP_RESOLUTION_PATTERN = re.compile(
    r"(?:ResolutionImpossible|No matching distribution found for|"
    r"Could not find a version that satisfies the requirement)\s+(?P<pkg>[a-zA-Z0-9_\.-]+)",
    re.IGNORECASE,
)

MODULE_NOT_FOUND_PATTERN = re.compile(r"ModuleNotFoundError: No module named \'(?P<mod>[^\']+)\'")


def strip_ansi_codes(text: str) -> str:
    """Removes ANSI color and formatting escape sequences from log output."""
    return ANSI_ESCAPE_PATTERN.sub("", text)


def filter_and_extract_signal(raw_log: str) -> FailureSignal:
    """Deterministically filters raw CI logs and extracts a structured FailureSignal."""
    cleaned_log = strip_ansi_codes(raw_log).replace("\r\n", "\n").replace("\r", "\n")
    lines = cleaned_log.splitlines()

    # 1. Check for ModuleNotFoundError
    mod_match = MODULE_NOT_FOUND_PATTERN.search(cleaned_log)
    if mod_match:
        mod_name = mod_match.group("mod")
        snippet = _extract_surrounding_lines(cleaned_log, mod_match.start(), window=6)
        target_file, target_line, frames = _extract_traceback_frames(cleaned_log)
        return FailureSignal(
            category=FailureCategory.DEPENDENCY,
            error_type="ModuleNotFoundError",
            message=f"No module named '{mod_name}'",
            target_file=target_file,
            target_line=target_line,
            extracted_snippet=snippet,
            traceback_frames=frames,
        )

    # 2. Check for Python Tracebacks
    tb_match = TRACEBACK_BLOCK_PATTERN.search(cleaned_log)
    if tb_match:
        exc_type = tb_match.group("exception")
        exc_msg = tb_match.group("msg").strip()
        target_file, target_line, frames = _extract_traceback_frames(cleaned_log)
        snippet = _extract_surrounding_lines(cleaned_log, tb_match.start(), window=8)

        category = FailureCategory.TEST if "assert" in exc_type.lower() else FailureCategory.BUILD
        return FailureSignal(
            category=category,
            error_type=exc_type,
            message=exc_msg,
            target_file=target_file,
            target_line=target_line,
            extracted_snippet=snippet,
            traceback_frames=frames,
        )

    # 3. Check for Pytest test failure lines
    pytest_match = PYTEST_FAILED_PATTERN.search(cleaned_log)
    if pytest_match:
        fpath = pytest_match.group("file")
        test_name = pytest_match.group("test")
        exc_type = pytest_match.group("exc") or "PytestAssertionError"
        err_msg = pytest_match.group("err") or f"Test failed: {test_name}"
        snippet = _extract_surrounding_lines(cleaned_log, pytest_match.start(), window=6)

        # Check for specific line reference in pytest summary
        target_line = None
        line_ref = PYTEST_LINE_REF_PATTERN.search(cleaned_log)
        if line_ref and line_ref.group("file") == fpath:
            target_line = int(line_ref.group("line"))
            if not pytest_match.group("exc"):
                exc_type = line_ref.group("exc")

        frames = []
        if fpath and target_line:
            frames.append(
                TracebackFrame(
                    file_path=fpath,
                    line_number=target_line,
                    function_name=test_name,
                    code_line=None,
                )
            )

        return FailureSignal(
            category=FailureCategory.TEST,
            error_type=exc_type,
            message=err_msg.strip(),
            target_file=fpath,
            target_line=target_line,
            extracted_snippet=snippet,
            traceback_frames=frames,
        )

    # 4. Check for Ruff / Linter errors
    ruff_match = RUFF_LINT_PATTERN.search(cleaned_log)
    if ruff_match:
        fpath = ruff_match.group("file")
        line_no = int(ruff_match.group("line"))
        rule_code = ruff_match.group("code")
        rule_msg = ruff_match.group("msg")
        snippet = _extract_surrounding_lines(cleaned_log, ruff_match.start(), window=5)
        return FailureSignal(
            category=FailureCategory.LINT,
            error_type=f"LintViolation({rule_code})",
            message=rule_msg.strip(),
            target_file=fpath,
            target_line=line_no,
            extracted_snippet=snippet,
            traceback_frames=[],
        )

    # 5. Check for Mypy / Type-checker errors
    mypy_match = MYPY_PATTERN.search(cleaned_log)
    if mypy_match:
        fpath = mypy_match.group("file")
        line_no = int(mypy_match.group("line"))
        type_msg = mypy_match.group("msg")
        snippet = _extract_surrounding_lines(cleaned_log, mypy_match.start(), window=5)
        return FailureSignal(
            category=FailureCategory.LINT,
            error_type="TypeError",
            message=type_msg.strip(),
            target_file=fpath,
            target_line=line_no,
            extracted_snippet=snippet,
            traceback_frames=[],
        )

    # 6. Check for Pip / Dependency Resolution Failures
    pip_match = PIP_RESOLUTION_PATTERN.search(cleaned_log)
    if pip_match:
        pkg_name = pip_match.group("pkg")
        snippet = _extract_surrounding_lines(cleaned_log, pip_match.start(), window=6)
        return FailureSignal(
            category=FailureCategory.DEPENDENCY,
            error_type="DependencyResolutionError",
            message=f"Resolution failed for package: {pkg_name}",
            target_file=None,
            target_line=None,
            extracted_snippet=snippet,
            traceback_frames=[],
        )

    # 7. Fallback: Generic error scanner
    candidate_lines = []
    error_markers = ["ERROR", "FAILED", "FATAL", "EXIT CODE 1", "EXCEPTION"]
    for i, line in enumerate(lines):
        if any(marker in line.upper() for marker in error_markers):
            candidate_lines.append((i, line))

    if candidate_lines:
        first_idx, first_line = candidate_lines[0]
        start_idx = max(0, first_idx - 3)
        end_idx = min(len(lines), first_idx + 8)
        snippet = "\n".join(lines[start_idx:end_idx])
        return FailureSignal(
            category=FailureCategory.UNKNOWN,
            error_type="GenericCIError",
            message=first_line.strip(),
            target_file=None,
            target_line=None,
            extracted_snippet=snippet,
            traceback_frames=[],
        )

    # Clean fallback when no error markers matched
    tail_excerpt = "\n".join(lines[-15:]) if len(lines) > 15 else cleaned_log
    return FailureSignal(
        category=FailureCategory.UNKNOWN,
        error_type="UnclassifiedFailure",
        message="Non-zero exit code detected; no standard error patterns identified",
        target_file=None,
        target_line=None,
        extracted_snippet=tail_excerpt,
        traceback_frames=[],
    )


def _extract_traceback_frames(
    log_text: str,
) -> tuple[str | None, int | None, list[TracebackFrame]]:
    """Extracts all frame objects and identifies the last application file/line."""
    frames: list[TracebackFrame] = []
    target_file = None
    target_line = None

    for match in FRAME_PATTERN.finditer(log_text):
        fpath = match.group("file")
        lineno = int(match.group("line"))
        func = match.group("func")
        code = match.group("code")

        frame = TracebackFrame(
            file_path=fpath,
            line_number=lineno,
            function_name=func.strip() if func else None,
            code_line=code.strip() if code else None,
        )
        frames.append(frame)
        target_file = fpath
        target_line = lineno

    return target_file, target_line, frames


def _extract_surrounding_lines(text: str, char_pos: int, window: int = 5) -> str:
    """Returns a slice of lines surrounding a specific character position."""
    lines = text.splitlines()
    cur_pos = 0
    target_line_idx = 0
    for idx, line in enumerate(lines):
        cur_pos += len(line) + 1
        if cur_pos >= char_pos:
            target_line_idx = idx
            break

    start = max(0, target_line_idx - window)
    end = min(len(lines), target_line_idx + window + 1)
    return "\n".join(lines[start:end])
