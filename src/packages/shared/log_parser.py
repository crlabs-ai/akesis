"""Deterministic CI log parsing into a FailureSignal.

The input is untrusted and can be up to `log_parse_max_chars` long, so every scan here is linear
in the length of the text. Python's `re` backtracks, and six former patterns were quadratic on
hostile input (a lazy DOTALL search restarted at every traceback header; greedy runs re-scanned
from every start position). Each is now a scanner that returns exactly what the former regex
returned: the same leftmost match, the same groups, the same backtracking choice. The derivation
is noted on each scanner and checked differentially against the former patterns in
tests/unit/test_log_parser_complexity.py.

Why the scanners are linear:
* Candidate starts are found with str.find or a possessive run regex (`++`), which never
  revisits a character.
* Everything a candidate examines after its start (a number, a whitespace run, a word, the rest
  of a line) belongs to that candidate alone, or is found through a cached forward pointer that
  only moves forward. So each character is examined a constant number of times.
"""

import re
from collections.abc import Iterator

from src.packages.shared.models import FailureCategory, FailureSignal, TracebackFrame

# ANSI escape sequence regex pattern. Linear: its three classes are disjoint, so nothing backtracks,
# and the run after one ESC cannot contain another.
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~])")

# Possessive building blocks: each consumes a maximal run and never gives a character back.
_WORD = re.compile(r"\w++")
_DIGITS = re.compile(r"\d++")
_SPACE = re.compile(r"\s++")
_PATH_RUN = re.compile(r"[a-zA-Z0-9_/.\-]++")  # the former `[a-zA-Z0-9_/\.-]` file class
_PYTEST_TEST = re.compile(r"[^\s-]++")
_RULE_CODE = re.compile(r"[A-Z0-9]++")

_TRACEBACK_HEADER = "Traceback (most recent call last):"
_TRACEBACK_SUFFIXES = ("Error", "Exception", "Warning", "Interrupt")
_TEST_SUFFIXES = ("Error", "Exception")

# Pip / dependency error patterns. Linear: they start at fixed literals and their runs are
# delimited by characters outside them.
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
    tb_match = _find_traceback(cleaned_log)
    if tb_match:
        tb_start, exc_type, exc_msg = tb_match
        exc_msg = exc_msg.strip()
        target_file, target_line, frames = _extract_traceback_frames(cleaned_log)
        snippet = _extract_surrounding_lines(cleaned_log, tb_start, window=8)

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
    pytest_match = _find_pytest_failure(cleaned_log)
    if pytest_match:
        pytest_start, fpath, test_name, pytest_exc, pytest_err = pytest_match
        exc_type = pytest_exc or "PytestAssertionError"
        err_msg = pytest_err or f"Test failed: {test_name}"
        snippet = _extract_surrounding_lines(cleaned_log, pytest_start, window=6)

        # Check for specific line reference in pytest summary
        target_line = None
        line_ref = _find_line_reference(cleaned_log)
        if line_ref and line_ref[0] == fpath:
            target_line = line_ref[1]
            if not pytest_exc:
                exc_type = line_ref[2]

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
    ruff_match = _find_lint_violation(cleaned_log)
    if ruff_match:
        ruff_start, fpath, line_no, rule_code, rule_msg = ruff_match
        snippet = _extract_surrounding_lines(cleaned_log, ruff_start, window=5)
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
    mypy_match = _find_type_error(cleaned_log)
    if mypy_match:
        mypy_start, fpath, line_no, type_msg = mypy_match
        snippet = _extract_surrounding_lines(cleaned_log, mypy_start, window=5)
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

    for fpath, lineno, func, code in _iter_frames(log_text):
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


# ---- linear scanners replacing the former backtracking patterns ---------------------------------
#
# Each docstring names the former pattern and the facts that make the scanner return the same
# match. The recurring one: when a greedy run is followed by a character outside it, backtracking
# can never help, so the run is maximal and a possessive match is equivalent.


def _match_end(pattern: re.Pattern[str], text: str, pos: int) -> int:
    """End of a possessive `pattern` matched at `pos`, or `pos` if it does not match there."""
    match = pattern.match(text, pos)
    return match.end() if match else pos


def _is_exception_word(word: str, suffixes: tuple[str, ...]) -> bool:
    """`\\w+(?:suffix|...)` then ':' on a whole word: a suffix with a character before it."""
    return any(len(word) > len(suffix) and word.endswith(suffix) for suffix in suffixes)


def _exception_prefix(word: str) -> str | None:
    """`\\w+(?:Error|Exception)` with nothing required after it, on the word at that position.

    Greedy `\\w+` gives characters back one at a time and tries each suffix at every split, so
    the match ends at the last suffix that starts at index 1 or later.
    """
    best: str | None = None
    best_start = -1
    for suffix in _TEST_SUFFIXES:
        start = word.rfind(suffix, 1)
        if start > best_start:
            best_start, best = start, word[: start + len(suffix)]
    return best


def _space_then_line(text: str, pos: int) -> tuple[int, int] | None:
    """`\\s+(.+)` at `pos`, without DOTALL: the span of the group, or None.

    The whitespace run is maximal unless it reaches the end of the text. Then the regex gives
    characters back until the group can start on one that is not a newline.
    """
    space_end = _match_end(_SPACE, text, pos)
    if space_end == pos:
        return None
    start = space_end
    if start == len(text):
        start -= 1
        while start > pos and text[start] == "\n":
            start -= 1
        if start == pos:
            return None
    end = text.find("\n", start)
    return start, len(text) if end == -1 else end


def _space_then_line_then_newline(text: str, pos: int, last_newline: int) -> tuple[int, int] | None:
    """`\\s+([^\\n]+)\\n` at `pos`: the span of the group; a newline follows it, or None.

    With a non-space character after the maximal whitespace run and a newline later, the group
    is that rest of the line. Otherwise only a newline inside the run can end the group, so the
    regex settles on the last non-newline character before the run's last newline.

    `last_newline` is text.rfind("\\n"). The forward search runs only when it must find a
    newline, and then this candidate matches through it; a search that would fail is skipped,
    because repeating it for every later candidate on a newline-free line was quadratic.
    """
    space_end = _match_end(_SPACE, text, pos)
    if space_end == pos:
        return None
    if space_end < len(text) and space_end < last_newline:
        return space_end, text.find("\n", space_end)
    last = text.rfind("\n", pos, space_end)
    if last == -1:
        return None
    start = last - 1
    while start > pos and text[start] == "\n":
        start -= 1
    if start <= pos:
        return None
    return start, start + 1


def _find_traceback(text: str) -> tuple[int, str, str] | None:
    """Former `Traceback \\(most recent call last\\):(?P<frames>.*?)
    (?P<exception>\\w+(?:Error|Exception|Warning|Interrupt)): (?P<msg>.+)` with DOTALL.

    Returns (start, exception, message). If any header has a match, the first header does, and
    the lazy gap stops at the first word after it that ends in a suffix (with one more character
    before it) and is followed by ": " and at least one character. DOTALL `.+` takes the rest of
    the text. The former regex re-ran that gap scan from every later header: quadratic.
    """
    start = text.find(_TRACEBACK_HEADER)
    if start == -1:
        return None
    for word in _WORD.finditer(text, start + len(_TRACEBACK_HEADER)):
        end = word.end()
        if (
            end + 2 < len(text)
            and text.startswith(": ", end)
            and _is_exception_word(word.group(), _TRACEBACK_SUFFIXES)
        ):
            return start, word.group(), text[end + 2 :]
    return None


def _iter_frames(text: str) -> Iterator[tuple[str, int, str | None, str | None]]:
    """Former FRAME_PATTERN, used with finditer: `File "(?P<file>[^"]+)", line (?P<line>\\d+)
    (?:,\\s*in\\s+(?P<func>[^\\n]+))?\\n(?:\\s+(?P<code>.+))?`.

    Yields (file, line, func, code) in order and continues after each match, as finditer does.
    The file runs to the first quote, which is at most the next candidate's own quote, so the
    file spans of different candidates never overlap.
    """
    last_newline = text.rfind("\n")
    pos = text.find('File "')
    while pos != -1:
        frame = _frame_at(text, pos, last_newline)
        if frame is None:
            pos = text.find('File "', pos + 1)
            continue
        values, end = frame
        yield values
        pos = text.find('File "', end)


def _frame_at(
    text: str, pos: int, last_newline: int
) -> tuple[tuple[str, int, str | None, str | None], int] | None:
    file_start = pos + len('File "')
    quote = text.find('"', file_start)
    if quote <= file_start or not text.startswith('", line ', quote):
        return None
    digits_start = quote + len('", line ')
    digits_end = _match_end(_DIGITS, text, digits_start)
    line = _line_number(text[digits_start:digits_end]) if digits_end > digits_start else None
    if line is None:
        return None
    func: str | None = None
    after: int | None = None
    if text.startswith(",", digits_end):
        in_pos = _match_end(_SPACE, text, digits_end + 1)
        if text.startswith("in", in_pos):
            span = _space_then_line_then_newline(text, in_pos + 2, last_newline)
            if span is not None:
                func = text[span[0] : span[1]]
                after = span[1] + 1
    if after is None:  # the optional group failed: the newline must follow the line number
        if not text.startswith("\n", digits_end):
            return None
        after = digits_end + 1
    code: str | None = None
    end = after
    code_span = _space_then_line(text, after)
    if code_span is not None:
        code = text[code_span[0] : code_span[1]]
        end = code_span[1]
    return (text[file_start:quote], line, func, code), end


def _find_pytest_failure(
    text: str,
) -> tuple[int, str, str, str | None, str | None] | None:
    """Former `FAILED (?P<file>[^:]+)::(?P<test>[^\\s-]+)
    (?: - (?:(?P<exc>\\w+(?:Error|Exception)):\\s*)?(?P<err>.*))?`.

    Returns (start, file, test, exc, err). The file runs to the first ':' after "FAILED ", which
    must begin "::". The test is a maximal run, and the optional tail never forces it shorter
    because skipping the tail always succeeds. The former regex rescanned to the next ':' from
    every "FAILED "; here that position is cached and only moves forward.
    """
    colon = -2  # first ':' at or after the current file start; -2 unknown, -1 none left
    pos = text.find("FAILED ")
    while pos != -1:
        file_start = pos + len("FAILED ")
        if colon != -1 and colon < file_start:
            colon = text.find(":", file_start)
        if colon == -1:
            return None
        if colon > file_start and text.startswith("::", colon):
            test_end = _match_end(_PYTEST_TEST, text, colon + 2)
            if test_end > colon + 2:
                exc: str | None = None
                err: str | None = None
                if text.startswith(" - ", test_end):
                    detail = test_end + 3
                    word = _WORD.match(text, detail)
                    if (
                        word is not None
                        and text.startswith(":", word.end())
                        and _is_exception_word(word.group(), _TEST_SUFFIXES)
                    ):
                        exc = word.group()
                        detail = _match_end(_SPACE, text, word.end() + 1)
                    line_end = text.find("\n", detail)
                    err = text[detail : len(text) if line_end == -1 else line_end]
                return pos, text[file_start:colon], text[colon + 2 : test_end], exc, err
        pos = text.find("FAILED ", pos + 1)
    return None


def _path_runs(text: str, extensions: tuple[str, ...]) -> Iterator[tuple[int, int]]:
    """Spans the former `(?P<file>[a-zA-Z0-9_/\\.-]+\\.(?:ext)):` could match, leftmost first.

    The file must be followed by ':', which is outside the class, so it ends where its maximal run
    ends. A run that can start a match can start it at its first character, so only run starts
    matter. The former pattern instead retried from every position inside a long run: quadratic.
    """
    for run in _PATH_RUN.finditer(text):
        name = run.group()
        if text.startswith(":", run.end()) and any(
            len(name) > len(ext) and name.endswith(ext) for ext in extensions
        ):
            yield run.start(), run.end()


def _line_number(digits: str) -> int | None:
    """The value of a digit run, or None when int() refuses it (over the 4300-digit limit).

    The former code let that ValueError escape the parser, so one hostile line failed the
    whole retrieval. A number that cannot be a line number now just fails to match.
    """
    try:
        return int(digits)
    except ValueError:
        return None


def _colon_number(text: str, pos: int) -> tuple[int, int] | None:
    """`:(\\d+)` at `pos`: (value, end). The run is maximal because a non-digit follows it."""
    if not text.startswith(":", pos):
        return None
    end = _match_end(_DIGITS, text, pos + 1)
    value = _line_number(text[pos + 1 : end]) if end > pos + 1 else None
    return (value, end) if value is not None else None


def _find_line_reference(text: str) -> tuple[str, int, str] | None:
    """Former `(?P<file>...\\.py):(?P<line>\\d+):\\s+(?P<exc>\\w+(?:Error|Exception))`.

    Returns (file, line, exc). Each candidate scans only its own number, whitespace and word.
    """
    for start, end in _path_runs(text, (".py",)):
        number = _colon_number(text, end)
        if number is None or not text.startswith(":", number[1]):
            continue
        word_start = _match_end(_SPACE, text, number[1] + 1)
        word = _WORD.match(text, word_start) if word_start > number[1] + 1 else None
        exc = _exception_prefix(word.group()) if word is not None else None
        if exc is not None:
            return text[start:end], number[0], exc
    return None


def _find_lint_violation(text: str) -> tuple[int, str, int, str, str] | None:
    """Former `(?P<file>...\\.py):(?P<line>\\d+):(?P<col>\\d+):\\s+(?P<code>[A-Z0-9]+)
    \\s+(?P<msg>.+)`. Returns (start, file, line, code, msg)."""
    for start, end in _path_runs(text, (".py",)):
        line = _colon_number(text, end)
        column = _colon_number(text, line[1]) if line is not None else None
        if line is None or column is None or not text.startswith(":", column[1]):
            continue
        code_start = _match_end(_SPACE, text, column[1] + 1)
        code_end = _match_end(_RULE_CODE, text, code_start)
        if code_start == column[1] + 1 or code_end == code_start:
            continue
        message = _space_then_line(text, code_end)
        if message is not None:
            code = text[code_start:code_end]
            return start, text[start:end], line[0], code, text[message[0] : message[1]]
    return None


def _find_type_error(text: str) -> tuple[int, str, int, str] | None:
    """Former `(?P<file>...\\.(?:py|ts|tsx)):(?P<line>\\d+):\\s+error:\\s+(?P<msg>.+)`.
    Returns (start, file, line, msg)."""
    for start, end in _path_runs(text, (".py", ".ts", ".tsx")):
        line = _colon_number(text, end)
        if line is None or not text.startswith(":", line[1]):
            continue
        error_at = _match_end(_SPACE, text, line[1] + 1)
        if error_at == line[1] + 1 or not text.startswith("error:", error_at):
            continue
        message = _space_then_line(text, error_at + len("error:"))
        if message is not None:
            return start, text[start:end], line[0], text[message[0] : message[1]]
    return None
