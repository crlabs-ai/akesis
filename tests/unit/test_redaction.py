"""Redaction: coverage equal to or wider than the former regexes, linear cost, no split secrets.

Complexity is checked three ways so no single brittle timing decides: a structural inventory of the
patterns, best-of-three scaling ratios (linear is ~4x for 4x input, quadratic ~16x), and one
generous absolute bound at the 2M parse limit.
"""

import random
import re
import time
from collections.abc import Callable

import pytest

from src.packages.shared import workflow_logs
from src.packages.shared.log_parser import filter_and_extract_signal as parse_log
from src.packages.shared.models import FailureSignal
from src.packages.shared.prompt_builder import untrusted_log_block
from src.packages.shared.workflow_logs import (
    MAX_EXCERPT_CHARS,
    REDACTED,
    LogLimits,
    extract_signal,
    redact,
    redact_tail,
)

# The former key=value regex, kept as the oracle for the scanner that replaces it.
OLD_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIALS?)"
    r"[A-Z0-9_]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
# The former token regexes. Everything they matched must be gone after redaction.
OLD_TOKENS = [
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}"),
    re.compile(r"://[^/\s:@]+:[^/\s@]+@"),
]
# The intended key=value grammar: the former one, except that a key starts where a run of key
# characters starts (not at a word boundary), escaped quotes do not close a value, and a value
# with no closing quote runs to the end of the text.
REFERENCE_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Z0-9_])([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|"
    r"CREDENTIALS?)[A-Z0-9_]*)(\s*[=:]\s*)"
    r"(\"(?:[^\"\\]|\\[\s\S])*\"|\"[\s\S]*|'(?:[^'\\]|\\[\s\S])*'|'[\s\S]*|\S+)"
)
# The token formats with no boundary, as they must now be recognised when glued to other text.
GLUED_TOKENS = [
    re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"xox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
]
LIMITS = LogLimits(
    max_compressed_bytes=1_000_000,
    max_entries=50,
    max_total_bytes=5_000_000,
    max_file_bytes=5_000_000,
    max_ratio=100,
    max_parse_chars=2_000_000,
    allowed_hosts=("actions.githubusercontent.com",),
)


# ---- coverage ------------------------------------------------------------------------------------


def _fuzz(alphabet: list[str], count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40))) for _ in range(count)]


ASSIGNMENT_ALPHABET = [
    "PASSWORD", "token", "Api_Key", "x", "_", "9", "=", ":", " ", "\n", '"', "'", "-", "\\",
    '\\"', "\\'", "\\\\", "é", "日", "ſECRET", "TO\u212aEN", "v",
]  # fmt: skip


def _reference_spans(text: str) -> list[list[int]]:
    """The reference grammar applied as the scanner applies it: every value is searched for
    further assignments (the next search starts at the value), and overlapping spans merge."""
    spans: list[list[int]] = []
    pos = 0
    while (m := REFERENCE_ASSIGNMENT.search(text, pos)) is not None:
        start, stop = m.span(3)
        pos = start
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], stop)
        else:
            spans.append([start, stop])
    return spans


def _reference_redact(text: str) -> str:
    out, last = [], 0
    for start, stop in _reference_spans(text):
        out += [text[last:start], REDACTED]
        last = stop
    return "".join([*out, text[last:]])


def test_assignment_scanner_matches_the_reference_grammar_exactly() -> None:
    for text in _fuzz(ASSIGNMENT_ALPHABET, 8000, seed=11):
        assert workflow_logs._redact_assignments(text) == _reference_redact(text), repr(text)


def test_assignment_redaction_covers_everything_the_former_regex_matched() -> None:
    # Every character the former regex redacted lies in a span the new grammar redacts (and the
    # scanner equals the new grammar exactly, above): coverage only widens.
    for text in _fuzz(ASSIGNMENT_ALPHABET, 8000, seed=12):
        covered = {i for start, stop in _reference_spans(text) for i in range(start, stop)}
        former = {i for m in OLD_ASSIGNMENT.finditer(text) for i in range(*m.span(3))}
        assert former <= covered, repr(text)


def test_token_redaction_covers_everything_the_former_patterns_matched() -> None:
    alphabet = [
        "ghp_", "github_pat_", "AKIA", "AIza", "xoxb-", "sk-", "eyJ", ".", "-", "_", "a", "B",
        "7", " ", "Bearer ", "basic ", "://", "u", ":", "@", "/", "=",
    ]  # fmt: skip
    for text in _fuzz(alphabet, 5000, seed=7):
        text = text * 3  # long enough to reach the minimum lengths
        out = redact(text)
        for pattern in OLD_TOKENS + GLUED_TOKENS:
            assert pattern.search(out) is None, (pattern.pattern, repr(text), repr(out))


CREDENTIALS = {
    "github classic": "ghp_" + "A1b2C3d4E5" * 3,
    "github fine-grained": "github_pat_" + "Y6u7I8o9P0" * 3,
    "aws access key": "AKIAQWERTYUIOPASDFGH",
    "aws session key": "ASIAQWERTYUIOPASDFGH",
    "google": "AIza" + "Z1x2C3v4B5" * 4,
    "slack": "xoxb-" + "N6m7L8k9J0" * 2,
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmN",
}


@pytest.mark.parametrize("name", CREDENTIALS)
@pytest.mark.parametrize("before", ["", "x", "Z", "_", "9", "é", "日", "f_", "token"])
@pytest.mark.parametrize("after", ["", "x", "é", "_", "Z9", "日"])
def test_credentials_glued_to_word_characters_are_redacted(
    name: str, before: str, after: str
) -> None:
    secret = CREDENTIALS[name]
    out = redact(f"start {before}{secret}{after} end")
    assert _leaks(out, secret) == [], out
    assert out.startswith(f"start {before}") and out.endswith(" end")


@pytest.mark.parametrize("before", ["_", "é", "日", " ", "="])
def test_sk_keys_need_only_a_non_alphanumeric_neighbour(before: str) -> None:
    secret = "sk-" + "H1g2F3d4S5" * 3
    assert "H1g2F3" not in redact(f"x{before}{secret}")


ORDINARY = [
    "risk-assessment-for-the-quarterly-plan",
    "disk-usage-reported-by-the-runner-host",
    "task-scheduler-configuration-loaded-ok",
    "nonbasic   configuration_value_here",
    "FAILED tests/test_risk-assessment-module.py::test_x - AssertionError: no",
    "pip install akia-utils==1.2.3 ghp_short AKIA",
    "the token was rotated and the password policy applied",
    'File "src/app/main.py", line 12, in <module>',
    "eyJ is a common prefix; eyJabc.eyJdef.ghi is too short",
]


def test_ordinary_text_is_not_redacted() -> None:
    for text in ORDINARY:
        assert redact(text) == text, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('PASSWORD="abc def ghi', "PASSWORD=[REDACTED]"),
        ("PASSWORD='abc def\nmore lines\nand more", "PASSWORD=[REDACTED]"),
        ('PASSWORD="abc def" tail', "PASSWORD=[REDACTED] tail"),
        ('PASSWORD="ab\\"cd ef" tail', "PASSWORD=[REDACTED] tail"),
        ('PASSWORD="ab\\\\" tail', "PASSWORD=[REDACTED] tail"),
        ("API_KEY='it\\'s a secret' tail", "API_KEY=[REDACTED] tail"),
        ('TOKEN="multi\nline value" tail', "TOKEN=[REDACTED] tail"),
        ('éDB_SECRET="a b c" tail', "éDB_SECRET=[REDACTED] tail"),
        (
            "A_TOKEN=\"x y\" B_SECRET='z w' C=ok D_PASSWD=p4ss E=\"keep me\" end",
            "A_TOKEN=[REDACTED] B_SECRET=[REDACTED] C=ok D_PASSWD=[REDACTED] E=\"keep me\" end",
        ),
    ],
    ids=[
        "unclosed double", "unclosed single across lines", "closed", "escaped quote",
        "escaped backslash then close", "escaped single quote", "closed across lines",
        "unicode-glued key", "several on one line",
    ],
)  # fmt: skip
def test_quoted_secret_values(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_unclosed_quote_scan_is_linear_and_redacts_the_rest() -> None:
    small = 'TOKEN="' + '\\"' * 10_000 + " tail"  # every quote escaped: no closing quote
    large = 'TOKEN="' + '\\"' * 40_000 + " tail"
    assert redact(large) == "TOKEN=[REDACTED]"
    t0 = time.perf_counter()
    redact(small)
    t1 = time.perf_counter()
    redact(large)
    t2 = time.perf_counter()
    assert (t2 - t1) < max(t1 - t0, 0.0005) * 10


def test_multiple_formats_in_one_input_are_all_redacted() -> None:
    secrets = {
        "ghp": "ghp_" + "Q1w2E3r4T5" * 3,
        "pat": "github_pat_" + "Y6u7I8o9P0" * 3,
        "aws": "AKIAQWERTYUIOPASDFGH",
        "gcp": "AIza" + "Z1x2C3v4B5" * 4,
        "slack": "xoxb-" + "N6m7L8k9J0" * 2,
        "openai": "sk-" + "H1g2F3d4S5" * 3,
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
        "bearer": "Bearer " + "R5t6Y7u8I9o0" * 2,
        "url": "postgres://admin:Pq9Wz8Xy7@db/x",
        "kv": "DB_PASSWORD=Vb6Nm5Kl4",
        "quoted": 'API_KEY="Jh3 Gf2 Ds1"',
        "key": "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34\n-----END RSA PRIVATE KEY-----",
    }
    out = redact(" | ".join(secrets.values()))
    fragments = "Q1w2E3 Y6u7I8 QWERTYUIOP Z1x2C3 N6m7L8 H1g2F3 dozjgNryP4 R5t6Y7 Pq9Wz8 Vb6Nm5"
    for fragment in [*fragments.split(), "Jh3 Gf2", "MIIBOgIBAAJBAKj34"]:
        assert fragment not in out, fragment
    assert "postgres://[REDACTED]@db/x" in out and "DB_PASSWORD=[REDACTED]" in out


KEY_TYPES = ["", "RSA ", "EC ", "DSA ", "OPENSSH ", "ENCRYPTED "]


def _key_body(lines: int) -> str:
    """Distinct base64-looking lines, so any surviving line is detectable."""
    return "\n".join(f"MII{i:06d}Ev{'Q' * 50}AB{i:06d}" for i in range(lines))


def _block(kind: str, body: str, end_kind: str | None = None) -> str:
    end = kind if end_kind is None else end_kind
    return f"-----BEGIN {kind}PRIVATE KEY-----\n{body}\n-----END {end}PRIVATE KEY-----"


def _body_lines_left(out: str, body: str) -> int:
    return sum(line in out for line in body.splitlines())


@pytest.mark.parametrize("kind", KEY_TYPES)
@pytest.mark.parametrize("lines", [1, 20, 200, 5_000], ids=["1 line", "20", "12 KiB", "5000 lines"])
def test_terminated_private_key_blocks_are_redacted_whole(kind: str, lines: int) -> None:
    body = _key_body(lines)
    out = redact(f"before {_block(kind, body)} after")
    assert out == f"before {REDACTED} after"
    assert _body_lines_left(out, body) == 0


@pytest.mark.parametrize("kind", KEY_TYPES)
def test_unterminated_private_key_redacts_the_rest_of_the_input(kind: str) -> None:
    body = _key_body(300)
    out = redact(f"before -----BEGIN {kind}PRIVATE KEY-----\n{body}\nlater log text")
    assert out == f"before {REDACTED}"


def test_multiple_private_key_blocks_are_redacted_separately() -> None:
    first, second, third = _key_body(5), _key_body(400), _key_body(3)
    text = f"x {_block('', first)} y {_block('OPENSSH ', second)} z {_block('EC ', third)} w"
    assert redact(text) == f"x {REDACTED} y {REDACTED} z {REDACTED} w"


def test_an_end_marker_of_another_key_type_does_not_close_the_block() -> None:
    body = _key_body(10)
    # No END of the same type anywhere: the rest of the input is redacted.
    lone = redact(f"a {_block('RSA ', body, end_kind='EC ')} tail")
    assert lone == f"a {REDACTED}"
    # A later END of the same type closes it; the foreign END is part of the block.
    text = (
        f"a {_block('RSA ', body, end_kind='EC ')}\nmore {body}\n-----END RSA PRIVATE KEY----- tail"
    )
    assert redact(text) == f"a {REDACTED} tail"


def test_end_marker_without_begin_is_left_alone() -> None:
    text = "log line -----END RSA PRIVATE KEY----- more"
    assert redact(text) == text


def test_private_key_scan_is_linear() -> None:
    """Each END is examined once: by the block it closes, or skipped inside it."""
    shapes: dict[str, Callable[[int], str]] = {
        "many unmatched END markers": lambda n: (
            "-----BEGIN RSA PRIVATE KEY-----\n" + _repeat("-----END EC PRIVATE KEY-----\n", n)
        ),
        "many small blocks": lambda n: _repeat(_block("EC ", "MII") + "\n", n),
        "nested types": lambda n: (
            _repeat("-----BEGIN DSA PRIVATE KEY-----\n", n // 2)
            + _repeat("-----END DSA PRIVATE KEY-----\n", n // 2)
        ),
        "headers with long type runs": lambda n: _repeat("-----BEGIN " + "A " * 40 + "x", n),
    }
    for name, make in shapes.items():
        small, large = _best(make(100_000)), _best(make(400_000))
        assert large < max(small, 0.0005) * 10, name  # linear ~4x for 4x input
        started = time.perf_counter()
        redact(make(LIMITS.max_parse_chars))
        assert time.perf_counter() - started < 5.0, name


# ---- complexity ----------------------------------------------------------------------------------


def test_every_pattern_is_possessive_or_fixed_length() -> None:
    """Structural guard: an unbounded quantifier that can backtrack brings quadratic cost back."""
    patterns = [p for p, _ in workflow_logs._TOKEN_PATTERNS + workflow_logs._CONTEXT_PATTERNS]
    patterns += [workflow_logs._ASSIGNMENT, workflow_logs._JWT_RUN, workflow_logs._NON_SPACE]
    for pattern in patterns:
        source = re.sub(r"\[(?:\\.|[^\]\\])*\]", "C", pattern.pattern)  # classes hold literal '+'
        quantifiers = re.findall(r"(?<!\\)(\{\d+,\}|\*|\+)(\+?)", source)
        for quantifier, possessive in quantifiers:
            assert possessive == "+", (pattern.pattern, quantifier)


def _best(text: str) -> float:
    times = []
    for _ in range(3):
        started = time.perf_counter()
        redact(text)
        times.append(time.perf_counter() - started)
    return min(times)


def _repeat(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


ADVERSARIAL = {
    "repeated PASSWORD_": "PASSWORD_",
    "repeated eyJ-": "eyJ-",
    "repeated AIza-": "AIza-",
    "unterminated keys": "-----BEGIN PRIVATE KEY-----\n",
    "unclosed quotes": 'PASSWORD="',
    "word and spaces": "TOKENISH" + " " * 8,
    "glued github prefixes": "aghp_",
    "glued aws prefixes": "AKIA",
    "glued sk prefixes": "_sk-",
    "glued jwt prefixes": "xeyJ.",
    "escaped quote runs": '\\"',
    "keys with escaped quotes": 'TOKEN="\\"',
    "unicode-glued keys": "éTOKEN=",
}


@pytest.mark.parametrize("unit", ADVERSARIAL.values(), ids=ADVERSARIAL.keys())
def test_adversarial_input_scales_linearly(unit: str) -> None:
    small, large = _best(_repeat(unit, 20_000)), _best(_repeat(unit, 80_000))
    # 4x the input: linear is ~4x, the former quadratic pattern was ~16x. 10x leaves room for noise.
    assert large < max(small, 0.0005) * 10


@pytest.mark.parametrize("size", [20_000, 40_000, 80_000])
def test_repeated_password_sequences_stay_fast(size: int) -> None:
    # Before the fix: 0.91s, 3.7s and 15.1s. Linear redaction is a few milliseconds.
    assert _best(_repeat("PASSWORD_", size)) < 0.5


def test_near_parse_limit_hostile_input_is_bounded() -> None:
    text = _repeat('PASSWORD_ eyJ-AIza- aghp_ AKIA _sk- xeyJ. \\" éTOKEN=', LIMITS.max_parse_chars)
    started = time.perf_counter()
    redact(text)
    assert time.perf_counter() - started < 10.0  # generous: a few hundred milliseconds in practice


def test_long_key_value_assignment_is_redacted_whole() -> None:
    out = redact("PASSWORD=" + "x" * 500_000 + " after")
    assert out == f"PASSWORD={REDACTED} after"


# ---- truncation boundaries ----------------------------------------------------------------------

LIMIT = 300
FILLER = "lorem ipsum dolor "
OLD_MARGIN = 8_448  # the former fixed overlap; several secrets below are longer on purpose
LONG_QUOTED = "Q9w8E7 r6T5y4 " * 700  # 9,800 characters, with spaces
SECRETS = {
    "github token": ("ghp_" + "A1b2C3d4E5" * 4, "A1b2C3d4E5" * 4),
    "key=value": ("CI_TOKEN=Vb6Nm5Kl4Jh3Gf2Ds1Ap0", "Vb6Nm5Kl4Jh3Gf2Ds1Ap0"),
    "key: value": ("password:   Wq1Er2Ty3Ui4Op5As6", "Wq1Er2Ty3Ui4Op5As6"),
    "aws key": ("AKIAQWERTYUIOPASDFGH", "QWERTYUIOPASDFGH"),
    "jwt": ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmN", "dozjgNryP4J3"),
    "private key": (
        "-----BEGIN PRIVATE KEY-----\n" + "\n".join(["MIIEvQIBADANBgkqhkiG"] * 8) + "\n"
        "-----END PRIVATE KEY-----",
        "MIIEvQIBADANBgkqhkiG",
    ),
    "quoted value with spaces": ('API_KEY="Jh3 Gf2 Ds1 Ap0 Lk9"', "Jh3 Gf2 Ds1 Ap0 Lk9"),
    "quoted value longer than the old margin": ('DB_SECRET="' + LONG_QUOTED + '"', LONG_QUOTED),
    "key, gap longer than the old margin, value": (
        "DB_PASSWORD" + " " * (OLD_MARGIN + 600) + "=" + "\n" * 40 + "  Zx9Cv8Bn7Mm6Ll5",
        "Zx9Cv8Bn7Mm6Ll5",
    ),
    "bearer, gap longer than the old margin": (
        "Authorization: Bearer" + " " * (OLD_MARGIN + 600) + "Pq1Wo2Ei3Ru4Ty5",
        "Pq1Wo2Ei3Ru4Ty5",
    ),
    "github token glued to an identifier": ("f_ghp_" + "Hb4Nj5Mk6L" * 4, "Hb4Nj5Mk6L" * 4),
    "aws key glued on both sides": ("idAKIAZXCVBNMLKJHGFDSAzzz", "ZXCVBNMLKJHGFDSA"),
    "key glued to a unicode letter": ("éDB_SECRET=Rt5Yu6Io7Pa8Sd9", "Rt5Yu6Io7Pa8Sd9"),
    "value with escaped quotes": ('API_KEY="Tu2 \\"Vw3\\" Xy4 Za5"', 'Tu2 \\"Vw3\\" Xy4 Za5'),
    "unterminated quoted value": ('API_TOKEN="Gh7 Jk8 Lm9 Np0 Qr1', "Gh7 Jk8 Lm9 Np0 Qr1"),
    "private key over 8 KiB": (
        _block("RSA ", "\n".join(["MIIEvQIBADANBgkqhkiG9w0B"] * 500)),
        "MIIEvQIBADANBgkqhkiG9w0B",
    ),
    "unterminated private key": (
        "-----BEGIN EC PRIVATE KEY-----\n" + "\n".join(["MHcCAQEEIBkg4LVWM9nuwNSk"] * 50),
        "MHcCAQEEIBkg4LVWM9nuwNSk",
    ),
}
# Growth a final pass can add: only a match that starts at the cut is new (see redact_tail).
SLACK = len(REDACTED) - 1


def _leaks(output: str, sensitive: str) -> list[str]:
    return [
        sensitive[i : i + 6] for i in range(len(sensitive) - 5) if sensitive[i : i + 6] in output
    ]


def _recognizable(output: str) -> list[str]:
    """Anything the former patterns would still call a secret, ignoring our own markers."""
    found = [m.group() for p in OLD_TOKENS + GLUED_TOKENS for m in p.finditer(output)]
    for grammar in (OLD_ASSIGNMENT, REFERENCE_ASSIGNMENT):
        found += [m.group() for m in grammar.finditer(output) if m.group(3) != REDACTED]
    return found


@pytest.mark.parametrize("name", SECRETS)
@pytest.mark.parametrize(
    "position", ["before", "at_start", "inside", "near_end", "at_end", "after"]
)
def test_truncation_never_leaves_a_credential_fragment(name: str, position: str) -> None:
    secret, sensitive = SECRETS[name]
    # The cut falls `offset` characters into the secret (negative: before it, len: after it).
    offset = {"before": -1, "at_start": 0, "inside": len(secret) // 2,
              "near_end": len(secret) - LIMIT // 2, "at_end": len(secret) - 1,
              "after": len(secret)}[position]  # fmt: skip
    # A secret longer than the window: the window then lies entirely inside it.
    suffix_len = max(LIMIT - (len(secret) - offset), 0)
    text = FILLER * 40 + secret + _repeat(" " + FILLER, suffix_len)
    out = redact_tail(text, LIMIT)
    assert len(out) <= LIMIT + SLACK
    assert _leaks(out, sensitive) == [], (position, out[:80])
    assert _recognizable(out) == []


def test_random_cuts_leave_nothing_recognizable() -> None:
    rng = random.Random(21)
    pieces = [s for s, _ in SECRETS.values()] + [FILLER, " ", "\n", "x", "é", '"', "=", ":"]
    for _ in range(300):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 30)))
        limit = rng.randint(1, 2_000)
        out = redact_tail(text, limit)
        assert len(out) <= limit + SLACK and _recognizable(out) == [], (limit, text[:120])
        # The cut guarantee covers secrets recognised in the full text. A secret glued to a word
        # character (e.g. "éDB_SECRET=") is not recognised at all, exactly as with the former
        # patterns; that is recognition coverage, checked by the oracle tests above.
        full = redact(text)
        for _, sensitive in SECRETS.values():
            if not _leaks(full, sensitive):
                assert _leaks(out, sensitive) == [], (limit, text[:120])


def test_a_long_single_token_excerpt_is_kept_not_emptied() -> None:
    # The former "drop the token cut mid-way" rule could erase a whole excerpt of one token.
    assert redact_tail("x" * 10_000, MAX_EXCERPT_CHARS) == "x" * MAX_EXCERPT_CHARS


# ---- storage time, at the maximum parse limit ----------------------------------------------------

FAILURE = (
    "\nFAILED tests/test_a.py::test_x - AssertionError: no\n"
    "##[error]Process completed with exit code 1.\n"
)
STORAGE_SECRETS = {
    "github token": SECRETS["github token"],
    "aws key": SECRETS["aws key"],
    "jwt": SECRETS["jwt"],
    "key=value": SECRETS["key=value"],
    "long quoted value with spaces": SECRETS["quoted value longer than the old margin"],
    "key, long whitespace, value": (
        "DB_PASSWORD" + " " * 10_000 + "=" + " " * 10 + "Zx9Cv8Bn7Mm6Ll5",
        "Zx9Cv8Bn7Mm6Ll5",
    ),
    "bearer, long whitespace": SECRETS["bearer, gap longer than the old margin"],
    "glued github token": SECRETS["github token glued to an identifier"],
    "glued aws key": SECRETS["aws key glued on both sides"],
    "unicode-glued key": SECRETS["key glued to a unicode letter"],
    "escaped quotes": SECRETS["value with escaped quotes"],
    "unterminated quoted value": SECRETS["unterminated quoted value"],
    "private key over 8 KiB": SECRETS["private key over 8 KiB"],
    "unterminated private key": SECRETS["unterminated private key"],
}
# An unclosed quote or private key redacts the rest of the file, failure lines included.
OVER_REDACTING = {"unterminated quoted value", "unterminated private key"}


def _log_cut_into(secret: str, into: int) -> str:
    """A log whose 2M parse window starts `into` characters into `secret` (clamped to it)."""
    into = min(max(into, 0), len(secret))
    lines = (MAX_PARSE_LIMIT - (len(secret) - into) - len(FAILURE)) // 100
    log = "noise\n" * 100 + secret + FAILURE + ("x" * 99 + "\n") * lines
    return log


MAX_PARSE_LIMIT = LIMITS.max_parse_chars


@pytest.mark.parametrize("name", STORAGE_SECRETS)
@pytest.mark.parametrize("where", ["before", "inside", "near_end"])
def test_storage_time_parse_window_never_stores_a_fragment(name: str, where: str) -> None:
    secret, sensitive = STORAGE_SECRETS[name]
    into = {"before": -50, "inside": len(secret) // 2, "near_end": len(secret) - 8}[where]
    log = _log_cut_into(secret, into)
    signal = extract_signal([("job/1_step.txt", log)], LIMITS)
    assert signal is not None
    if name in OVER_REDACTING:
        assert "test_a.py" not in signal.model_dump_json() and REDACTED in signal.extracted_snippet
    else:
        assert "test_a.py" in signal.extracted_snippet  # the secret's neighbour survives
    stored = signal.model_dump_json()
    assert _leaks(stored, sensitive) == [] and _recognizable(signal.extracted_snippet) == []


def test_parser_still_sees_at_most_the_parse_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = parse_log

    def spy(text: str) -> FailureSignal:
        seen.append(len(text))
        return real(text)

    monkeypatch.setattr(workflow_logs, "filter_and_extract_signal", spy)
    log = "PASSWORD=" + "a" * 3_000_000 + FAILURE  # longer than the limit, secrets at the start
    extract_signal([("job/1_step.txt", log)], LIMITS)
    assert seen and seen[0] <= LIMITS.max_parse_chars + SLACK


def test_every_stored_field_is_bounded_and_redacted() -> None:
    token = "ghp_" + "K1l2M3n4O5" * 3
    frame_path = "src/" + "d/" * 2_000 + token + ".py"
    log = (
        "Traceback (most recent call last):\n"
        f'  File "{frame_path}", line 3, in run {token}\n    call({token}, password={token})\n'
        f"ValueError: {token} " + "m" * 10_000 + "\n"
    )
    signal = extract_signal([("job/1_step.txt", log)], LIMITS)
    assert signal is not None
    frame = signal.traceback_frames[0]
    for value in (signal.target_file, frame.file_path, frame.function_name, frame.code_line):
        assert value is not None and len(value) <= workflow_logs.MAX_FIELD_CHARS + SLACK
    assert len(signal.message) <= workflow_logs.MAX_MESSAGE_CHARS + SLACK
    assert "K1l2M3" not in signal.model_dump_json()


# ---- prompt time --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", SECRETS)
def test_prompt_time_cuts_never_send_a_fragment(name: str) -> None:
    secret, sensitive = SECRETS[name]
    # Cut 40 characters before the end of the secret, so its start is far outside the excerpt.
    snippet = secret + " " + "z" * (MAX_EXCERPT_CHARS - 40)
    message = secret + " " + "z" * (workflow_logs.MAX_MESSAGE_CHARS - 40)
    block = untrusted_log_block(FailureSignal(extracted_snippet=snippet, message=message))
    assert _leaks(block, sensitive) == []
