"""Bounded-time log parsing: identical output to the former regexes, linear cost, safe failure.

Equivalence is checked differentially against the parser as it was before the rewrite
(tests/support/legacy_log_parser.py), scanner by scanner and for the whole function. Complexity
is checked structurally, by scaling ratios (linear ~4x for 4x input, quadratic ~16x), and with
generous absolute bounds at the maximum parse size, so no single timing decides.
"""

import asyncio
import random
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from src.packages.shared import log_parser, workflow_logs
from src.packages.shared.log_parser import filter_and_extract_signal
from src.packages.shared.models import FailureCategory
from tests.support import legacy_log_parser as legacy
from tests.unit.test_workflow_logs import FakeGitHubActions, _limits, _run, _zip

FIXTURES = Path(__file__).parent.parent / "fixtures"
MAX_PARSE_CHARS = 2_000_000  # Settings.log_parse_max_chars default
HEADER = "Traceback (most recent call last):\n"

# ---- former patterns as oracles ------------------------------------------------------------------


def _old_traceback(text: str) -> Any:
    m = legacy.TRACEBACK_BLOCK_PATTERN.search(text)
    return None if m is None else (m.start(), m.group("exception"), m.group("msg"))


def _old_frames(text: str) -> Any:
    return [
        (m.group("file"), int(m.group("line")), m.group("func"), m.group("code"))
        for m in legacy.FRAME_PATTERN.finditer(text)
    ]


def _old_pytest(text: str) -> Any:
    m = legacy.PYTEST_FAILED_PATTERN.search(text)
    if m is None:
        return None
    return m.start(), m.group("file"), m.group("test"), m.group("exc"), m.group("err")


def _old_line_ref(text: str) -> Any:
    m = legacy.PYTEST_LINE_REF_PATTERN.search(text)
    return None if m is None else (m.group("file"), int(m.group("line")), m.group("exc"))


def _old_ruff(text: str) -> Any:
    m = legacy.RUFF_LINT_PATTERN.search(text)
    if m is None:
        return None
    return m.start(), m.group("file"), int(m.group("line")), m.group("code"), m.group("msg")


def _old_mypy(text: str) -> Any:
    m = legacy.MYPY_PATTERN.search(text)
    return None if m is None else (m.start(), m.group("file"), int(m.group("line")), m.group("msg"))


PAIRS = {
    "traceback": (_old_traceback, log_parser._find_traceback),
    "frames": (_old_frames, lambda t: list(log_parser._iter_frames(t))),
    "pytest": (_old_pytest, log_parser._find_pytest_failure),
    "line_ref": (_old_line_ref, log_parser._find_line_reference),
    "ruff": (_old_ruff, log_parser._find_lint_violation),
    "mypy": (_old_mypy, log_parser._find_type_error),
}

# Tokens chosen to reach every branch: headers, quotes, separators, suffixes, digits (including
# non-ASCII decimal digits), Unicode letters and whitespace, and the characters each class excludes.
TOKENS = [
    "Traceback (most recent call last):", 'File "', '"', '", line ', ", in ", ",", "in", " in ",
    "12", "7", "٣", " ", "  ", "\n", "\n\n", "\t", " ", ":", ": ", "::", "FAILED ", " - ",
    "-", "ValueError", "Error", "XException", "Exception", "Warning", "KeyboardInterrupt",
    "AssertionError", "a", "Z", "_", "é", ".py", ".ts", ".tsx", "/", "src/app", ".", "error:",
    " error: ", "E501", "x", "test_x", "'",
]  # fmt: skip


def _fuzz(count: int, seed: int, tokens: list[str] = TOKENS, size: int = 24) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(tokens) for _ in range(rng.randint(1, size))) for _ in range(count)]


# Per-pattern templates: each slot holds valid pieces and near-misses; a case picks one per slot
# and then mutates the result. Random concatenation of tokens almost never forms e.g. a valid
# "path.py:N:" reference, so it would test nothing; the hit-rate assertion guards against that.
_NOISE = [" ", "  ", "\n", "\t", "\u2003", "x", "é", "_", "-", ":", '"', "'", "٣"]
TEMPLATES: dict[str, list[list[str]]] = {
    "traceback": [
        ["", "x\n", "ValueError: early\n"],
        [HEADER, HEADER.strip(), "Traceback (most recent call last):x"],
        ["", '  File "a.py", line 1, in f\n    g()\n', "\n", HEADER],
        ["ValueError", "XError", "Error", "KeyboardInterrupt", "UserWarning", "Warnings",
         "ErrorX", "é_Exception", "a.Error"],
        [": ", ":", ":  ", "", ": \n"],
        ["msg", "", "\n", "m\nKeyError: k"],
    ],
    "frames": [
        ["", "  ", '"'],
        ['File "', 'File  "', 'file "'],
        ["a.py", "src/b c.py", "", 'x"y', "a\nb"],
        ['", line ', '",line ', '", line'],
        ["1", "23", "٣", "", "9x"],
        ["", ",", ", ", ",\n", ",  "],
        ["", "in", " in", "inx", "\nin", "in "],
        ["", " ", "\t", "\n", "  \n \n", "\u2003", " \n"],
        ["f", "<module>", "", " "],
        ["\n", "", "\n\n", " \n"],
        ["", "    code()", "  \n", "\u2003c", " ", "\n\n  x"],
        ["", "\n", 'File "b.py", line 2\n', 'File "c.py", line 3, in g\n'],
    ],
    "pytest": [
        ["", "x\n", "FAILED", "FAILED a\n"],
        ["FAILED "],
        ["tests/a.py", "", "a:b", "t s", "\n"],
        ["::", ":", "", ":::"],
        ["test_x", "t[1-2]", "", "-t", " t", "t:x"],
        ["", " - ", " -", "-", "  - "],
        ["", "ValueError", "XException", "Error", "é", "Err"],
        ["", ":", ": ", "::"],
        ["", "  ", "\n  ", "msg", " \n"],
        ["", "\n", "msg\nmore"],
    ],
    "line_ref": [
        ["", "x ", "\n", "a.py:2: x\n"],
        ["a", "src/a", "", "é", "a-b/c"],
        [".py", ".pyc", ".p", "", ".py.py"],
        [":", "", "::"],
        ["1", "٣", "", "x", "12"],
        [":", "", ":1:"],
        [" ", "\n", "\t", "", "  \n", "\u2003"],
        ["ValueError", "XException", "ErrorX", "Error", "KeyErrorException", "ValueErrorErrorx",
         "é", "Exception"],
        ["", " tail", ":", "\n"],
    ],
    "ruff": [
        ["", "x\n", "a.py:1:2:\n"],
        ["a", "src/a", "é"],
        [".py", ".pyx", ""],
        [":"],
        ["1", "12", ""],
        [":", ""],
        ["5", ""],
        [":", ""],
        [" ", "", "\n", "  ", "\u2003"],
        ["E501", "e501", "F", "", "E5 01"],
        [" ", "", "\n", "  \n", "\t", "\n\n", " \n "],
        ["msg", "", " ", "\n"],
        ["", "\n", "\n\n"],
    ],
    "mypy": [
        ["", "x\n"],
        ["a", "src/a", "é"],
        [".py", ".ts", ".tsx", ".t", "", ".tsxx"],
        [":"],
        ["1", "", "٣"],
        [":", ""],
        [" ", "", "\n", "\u2003"],
        ["error:", "error", "error: ", "Error:"],
        [" ", "", "\n", " \n", "\n\n"],
        ["msg", "", " ", "\n"],
        ["", "\n"],
    ],
}  # fmt: skip


def _structured(name: str, count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    slots = TEMPLATES[name]
    pool = [piece for slot in slots for piece in slot] + _NOISE
    cases = []
    for _ in range(count):
        pieces = []
        for _ in range(rng.choice([1, 1, 1, 2, 3])):  # sometimes several instances in a row
            # The first piece of every slot is valid; favour it so most cases are real matches.
            pieces += [slot[0] if rng.random() < 0.6 else rng.choice(slot) for slot in slots]
        for _ in range(rng.choice([0, 0, 1, 2])):  # near-misses: replace, drop or insert a piece
            at = rng.randrange(len(pieces))
            op = rng.choice(["replace", "drop", "insert"])
            if op == "replace":
                pieces[at] = rng.choice(pool)
            elif op == "drop":
                pieces.pop(at)
            else:
                pieces.insert(at, rng.choice(pool))
        cases.append("".join(pieces))
    return cases


@pytest.mark.parametrize("name", PAIRS)
def test_each_scanner_matches_its_former_regex(name: str) -> None:
    old, new = PAIRS[name]
    cases = _structured(name, 20_000, seed=sum(map(ord, name)))
    hits = 0
    for text in cases:
        expected = old(text)
        hits += bool(expected)
        assert new(text) == expected, (name, text)
    assert hits > len(cases) // 5, f"fuzz too weak for {name}: {hits} matches"
    for text in _fuzz(5_000, seed=7, size=24):  # cross-pattern noise
        assert new(text) == old(text), (name, text)


EDGE_CASES = [
    "",
    HEADER,
    HEADER + "ValueError: ",
    HEADER + "ValueError: x",
    HEADER + "Error: no prefix\nKeyError: second",
    HEADER + "ValueErrorX: not a suffix end\nOSError: real",
    "ValueError: before any header\n" + HEADER + '  File "a.py", line 1\nTypeError: t',
    HEADER + HEADER + '  File "a.py", line 2, in f\n    g()\nRuntimeError: nested',
    'File "a.py", line 3, in f',
    'File "a.py", line 3, in f\n',
    'File "a.py", line 3, in    \n  \n',
    'File "a.py", line 3, in  \t\n\n',
    'File "a.py", line 3\n   ',
    'File "a.py", line 3\n \n x',
    'File "", line 3\n',
    'File "a.py", line 3, in f\n    code()\nFile "b.py", line 4\n',
    "FAILED ",
    "FAILED a::",
    "FAILED a::t - ",
    "FAILED a::t - ValueError:   \n  next",
    "FAILED a::t - ValueError:",
    "FAILED a:b::t",
    "FAILED FAILED a::t",
    "a.py:1: ValueErrorXError",
    "a.py:1:\n\nKeyException",
    "x.py:1:2: E501\n",
    "x.py:1:2: E501 \n",
    "x.py:1:2: E501  ",
    "x.py:1:2: E501\n \n",
    "x.py:1:2:E501 msg",
    "src/x.tsx:9: error:  msg",
    "src/x.tsx:9: error:\n",
    "src/x.ts:9:error: msg",
    ".py:1: error: m",
]


@pytest.mark.parametrize("text", EDGE_CASES, ids=range(len(EDGE_CASES)))
def test_edge_cases_match_the_former_regexes(text: str) -> None:
    for name, (old, new) in PAIRS.items():
        assert new(text) == old(text), name


# ---- whole-parser equivalence --------------------------------------------------------------------

REALISTIC = {
    "plain traceback": (
        "Run python -m app\n" + HEADER
        + '  File "/home/runner/work/app/main.py", line 12, in <module>\n    main()\n'
        '  File "/home/runner/work/app/main.py", line 8, in main\n    1 / 0\n'
        "ZeroDivisionError: division by zero\n##[error]Process completed with exit code 1.\n"
    ),
    "chained tracebacks": (
        HEADER + '  File "a.py", line 3, in load\n    data = read()\nKeyError: \'cfg\'\n\n'
        "During handling of the above exception, another exception occurred:\n\n" + HEADER
        + '  File "a.py", line 5, in load\n    raise ConfigError("missing")\n'
        "app.errors.ConfigError: missing\n"
    ),
    "keyboard interrupt": HEADER + '  File "w.py", line 1, in <module>\nKeyboardInterrupt: \n',
    "warning as error": HEADER + '  File "w.py", line 9, in f\nDeprecationWarning: old api\n',
    "pytest with line ref": (
        "FAILED tests/test_calc.py::test_add - AssertionError: assert 3 == 4\n"
        "tests/test_calc.py:7: AssertionError\n"
    ),
    "pytest without exc": "FAILED tests/test_io.py::test_read\ntests/test_io.py:21: OSError\n",
    "ruff": "src/app/main.py:12:5: F401 `os` imported but unused\nFound 1 error.\n",
    "mypy ts": "web/src/App.tsx:40: error: Type 'string' is not assignable\n",
}  # fmt: skip


def _corpus() -> dict[str, str]:
    corpus = {path.name: path.read_text(encoding="utf-8") for path in FIXTURES.glob("logs_*.txt")}
    corpus.update(REALISTIC)
    for i, text in enumerate(_fuzz(3_000, seed=99, size=40)):
        corpus[f"fuzz-{i}"] = text
    return corpus


def test_parser_output_is_identical_to_the_former_parser() -> None:
    corpus = _corpus()
    assert len([n for n in corpus if n.startswith("logs_")]) >= 6  # the real fixtures are present
    for name, text in corpus.items():
        assert filter_and_extract_signal(text) == legacy.filter_and_extract_signal(text), name


def test_multiple_tracebacks_keep_first_header_and_first_exception() -> None:
    signal = filter_and_extract_signal(REALISTIC["chained tracebacks"])
    assert signal.error_type == "KeyError"
    assert signal.message.startswith("'cfg'")  # DOTALL `.+`: the message runs to the end
    assert [f.line_number for f in signal.traceback_frames] == [3, 5]
    assert (signal.target_file, signal.target_line) == ("a.py", 5)


# ---- adversarial and malformed input -------------------------------------------------------------


def _repeat(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


ADVERSARIAL = {
    "repeated traceback headers": HEADER,
    "traceback-like lines": HEADER + '  File "x.py", line 1, in f\n    f()\n',
    "incomplete frames": 'File "a.py", line 1, in ',
    "unterminated file quote": 'File "a',
    "failed without separator": "FAILED a\n",
    "long path run": "a/",
    "path then digits": "a.py:1",
    "malformed nesting": "Traceback (most recent call last):Traceback (most recent call last):\n",
}


@pytest.mark.parametrize("unit", ADVERSARIAL.values(), ids=ADVERSARIAL.keys())
def test_adversarial_shapes_match_the_former_parser_at_small_sizes(unit: str) -> None:
    text = _repeat(unit, 3_000)
    assert filter_and_extract_signal(text) == legacy.filter_and_extract_signal(text)


def _best(text: str) -> float:
    times = []
    for _ in range(3):
        started = time.perf_counter()
        filter_and_extract_signal(text)
        times.append(time.perf_counter() - started)
    return min(times)


@pytest.mark.parametrize("unit", ADVERSARIAL.values(), ids=ADVERSARIAL.keys())
def test_adversarial_shapes_scale_linearly(unit: str) -> None:
    small, large = _best(_repeat(unit, 20_000)), _best(_repeat(unit, 80_000))
    assert large < max(small, 0.0005) * 10  # linear ~4x; the former parser was ~16x


@pytest.mark.parametrize("size", [20_000, 40_000, 80_000])
def test_repeated_traceback_headers_stay_fast(size: int) -> None:
    # Former parser: 0.46s at 20k and 7.46s at 80k.
    assert _best(_repeat(HEADER, size)) < 0.5


@pytest.mark.parametrize("unit", ADVERSARIAL.values(), ids=ADVERSARIAL.keys())
def test_maximum_parse_size_is_bounded(unit: str) -> None:
    text = _repeat(unit, MAX_PARSE_CHARS)
    started = time.perf_counter()
    filter_and_extract_signal(text)
    assert time.perf_counter() - started < 10.0  # generous; well under a second in practice


NEWLINE_FREE_FRAMES = [
    'File "a", line 1, in f ',
    'File "a", line 1, in  ',
    'File "a", line 1, in\t',
]


@pytest.mark.parametrize("unit", NEWLINE_FREE_FRAMES)
def test_frame_candidates_after_a_header_with_no_newline_left_are_linear(unit: str) -> None:
    # Every candidate needs a newline that never comes. A per-candidate search for it was
    # quadratic, but at memchr speed: invisible below ~100k characters, 1.3s at the 2M limit.
    small_text = HEADER + _repeat(unit, 3_000)
    assert list(log_parser._iter_frames(small_text)) == _old_frames(small_text)

    def best(size: int) -> float:
        text = HEADER + _repeat(unit, size)
        times = []
        for _ in range(3):
            started = time.perf_counter()
            list(log_parser._iter_frames(text))
            times.append(time.perf_counter() - started)
        return min(times)

    small, large = best(250_000), best(1_000_000)
    assert large < max(small, 0.0005) * 8  # linear ~4x; the per-candidate search was ~11x
    text = HEADER + _repeat(unit, MAX_PARSE_CHARS) + "\nValueError: x"
    started = time.perf_counter()
    filter_and_extract_signal(text)
    assert time.perf_counter() - started < 5.0


def _long_frame_log(scale: int) -> tuple[str, str]:
    path = "src/" + "d/" * scale + "m.py"
    text = HEADER + f'  File "{path}", line 4, in ' + "f" * (2 * scale) + "\n    "
    return text + "c" * (2 * scale) + "\nValueError: " + "m" * (4 * scale), path


def test_very_long_frames_and_messages() -> None:
    # The former parser is itself quadratic on this shape, so compare at a size it can finish.
    small, _ = _long_frame_log(2_000)
    assert filter_and_extract_signal(small) == legacy.filter_and_extract_signal(small)
    text, path = _long_frame_log(50_000)
    started = time.perf_counter()
    signal = filter_and_extract_signal(text)
    assert time.perf_counter() - started < 5.0
    assert (signal.target_file, signal.error_type) == (path, "ValueError")
    assert signal.traceback_frames[0].function_name == "f" * 100_000


def test_empty_and_oversized_input() -> None:
    assert filter_and_extract_signal("") == legacy.filter_and_extract_signal("")
    signal = filter_and_extract_signal("x" * (MAX_PARSE_CHARS * 2))
    assert signal.category == FailureCategory.UNKNOWN


@pytest.mark.parametrize(
    "text",
    [
        HEADER + '  File "a.py", line ' + "9" * 5_000 + ", in f\n    x()\nValueError: boom\n",
        "a.py:" + "9" * 5_000 + ":1: E501 too long\n",
        "FAILED a.py::t\na.py:" + "9" * 5_000 + ": ValueError\n",
        "src/a.ts:" + "9" * 5_000 + ": error: bad\n",
    ],
    ids=["frame", "ruff", "pytest line ref", "mypy"],
)
def test_line_numbers_int_cannot_hold_fail_closed_instead_of_raising(text: str) -> None:
    with pytest.raises(ValueError):  # the former parser let this escape
        legacy.filter_and_extract_signal(text)
    signal = filter_and_extract_signal(text)
    assert all(len(str(f.line_number)) < 100 for f in signal.traceback_frames)
    assert signal.target_line is None or len(str(signal.target_line)) < 100


def test_arbitrary_unicode_never_raises() -> None:
    rng = random.Random(5)
    alphabet = [chr(c) for c in range(0x20, 0x3000) if not 0xD800 <= c < 0xE000] + TOKENS
    for _ in range(300):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 400)))
        filter_and_extract_signal(text)


# ---- structure -----------------------------------------------------------------------------------


def test_former_quadratic_patterns_are_gone_and_the_rest_cannot_backtrack() -> None:
    for removed in ("TRACEBACK_BLOCK_PATTERN", "FRAME_PATTERN", "PYTEST_FAILED_PATTERN",
                    "PYTEST_LINE_REF_PATTERN", "RUFF_LINT_PATTERN", "MYPY_PATTERN"):  # fmt: skip
        assert not hasattr(log_parser, removed)
    # Linear for the reasons given next to each in log_parser.py: fixed-literal starts and runs
    # delimited by characters outside them.
    reviewed = {"ANSI_ESCAPE_PATTERN", "PIP_RESOLUTION_PATTERN", "MODULE_NOT_FOUND_PATTERN"}
    for name, value in vars(log_parser).items():
        if not isinstance(value, re.Pattern) or name in reviewed:
            continue
        source = re.sub(r"\[(?:\\.|[^\]\\])*\]", "C", value.pattern)
        for _quantifier, possessive in re.findall(r"(?<!\\)(\{\d+,\}|\*|\+)(\+?)", source):
            assert possessive == "+", (name, value.pattern)


# ---- worker integration --------------------------------------------------------------------------


def _hostile_archive(size: int) -> bytes:
    # Varied enough to pass the compression-ratio limit, hostile in shape.
    rng = random.Random(3)
    lines = [HEADER + f"FAILED {rng.getrandbits(64):x}\n" for _ in range(size // 60)]
    return _zip({"job/1_test.txt": "".join(lines).encode()})


def _big_limits() -> Any:
    return _limits(
        max_compressed_bytes=50_000_000,
        max_total_bytes=50_000_000,
        max_file_bytes=10_000_000,
        max_parse_chars=MAX_PARSE_CHARS,
        max_ratio=1_000,
    )


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_while_a_maximum_size_log_is_parsed() -> None:
    fake = FakeGitHubActions(archive=_hostile_archive(MAX_PARSE_CHARS))
    gaps: list[float] = []
    done = asyncio.Event()

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    outcome = await _run(fake, limits=_big_limits())
    done.set()
    await beat
    assert outcome.status == "extracted"
    assert len(gaps) >= 3 and max(gaps) < 1.0  # the loop kept running during the parse


@pytest.mark.asyncio
async def test_timeout_returns_promptly_and_abandoned_parse_work_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finished = threading.Event()
    real = workflow_logs.extract_signal

    def slow_extract(*args: Any) -> Any:
        try:
            time.sleep(0.3)  # longer than the deadline below
            return real(*args)
        finally:
            finished.set()

    monkeypatch.setattr(workflow_logs, "extract_signal", slow_extract)
    fake = FakeGitHubActions(archive=_hostile_archive(200_000))
    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await _run(fake, limits=_big_limits())
    assert time.perf_counter() - started < 0.25  # cancellation did not wait for the thread
    # The abandoned thread runs to completion in bounded time; nothing is left running.
    assert await asyncio.to_thread(finished.wait, 10.0)


@pytest.mark.asyncio
async def test_malformed_archive_text_fails_safely_through_the_worker_path() -> None:
    text = HEADER + '  File "a.py", line ' + "9" * 5_000 + "\nValueError: boom\n"
    fake = FakeGitHubActions(archive=_zip({"job/1_test.txt": text.encode()}))
    outcome = await _run(fake)
    assert outcome.status == "extracted" and outcome.signal is not None
