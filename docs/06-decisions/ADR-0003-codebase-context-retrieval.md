# ADR-0003: Deterministic Codebase Context Retrieval & Exact Commit Alignment

## Status
Accepted

## Date
2026-08-30

## Context
In Akesis Phase 2, the Gemini diagnostic engine operated primarily on CI log outputs and parsed traceback frames. Without direct access to the underlying repository source code corresponding to the failing commit, diagnostic reasoning was limited when logs lacked surrounding context.

In Phase 3, we needed to supply verified, bounded source code evidence to the diagnostic layer while strictly adhering to V1 boundaries:
1. Exact commit alignment (ensuring code matches the exact failing run).
2. Deterministic file discovery and bounded window extraction without semantic/vector databases.
3. Strict path traversal and security boundaries against untrusted input.
4. Passive untrusted data boundaries protecting system prompts against prompt injection inside code comments or files.

## Decision
1. **Exact Commit Checkout**: We implement `GitRepositoryCheckoutManager` using GitPython to fetch and checkout the exact `commit_sha` recorded in `FailureContext`.
2. **Failure-Path-Based Discovery**: We resolve candidate source files deterministically from `FailureSignal.target_file` and `FailureSignal.traceback_frames`.
3. **Bounded Context Windowing**: We extract a bounded slice of source lines (default 40 lines) centered around `target_line` with line numbers, rather than ingesting entire files.
4. **Security & Path Validation**: All candidate paths are normalized (stripping CI runner prefixes) and verified using `is_path_safe_and_within_root` to prevent directory traversal (`..`, absolute paths, symlink escapes).
5. **Evidence Packaging**: Structured `CodeEvidence` is assembled into an `EvidencePackage` alongside the CI `FailureContext`.

## Consequences
### Positive
* Highly explainable: Every inspected source file is directly attributable to failure signal evidence.
* Secure: Untrusted repository content cannot trigger directory escapes or redefine system instructions.
* Budget-bounded: Strict line, file count, and character limits protect token budgets and prevent large binary ingestion.

### Negative / Limitations
* In V1, cross-file semantic navigation or deep AST parsing is not supported; retrieval is strictly bounded to paths present in failure logs and tracebacks.
