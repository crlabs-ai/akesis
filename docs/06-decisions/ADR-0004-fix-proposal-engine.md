# ADR-0004: Fix Proposal Engine & Deterministic Patch Validation

## Status
Accepted

## Date
2026-08-30

## Context
Following Phase 2 (Diagnostic Baseline) and Phase 3 (Codebase Context Retrieval), Akesis needed the capability to generate reviewable, concrete code remediation proposals for verified CI failures.

Key constraints:
1. The LLM must not directly mutate the repository or execute shell commands.
2. Patches must be represented in a standard, canonical format (unified diff) suitable for later sandbox validation (Phase 5) and human approval (Phase 6).
3. Candidate target files must be grounded in failure/diagnostic evidence or verified to exist inside the checked-out repository.
4. Protected files (`.github/workflows/`, `.git/`, `.env*`) and dangerous operations (path traversal, huge diffs) must be rejected deterministically.

## Decision
1. **Canonical Representation**: We use standard Git unified diffs parsed into structured `FilePatch` and `PatchHunk` models.
2. **Deterministic PatchValidator**: We enforce syntax validation, refined target-file grounding (evidence-grounded OR verified to exist in repository), size budgets (max 2 files, max 100 lines, max 4000 chars), and protected file boundaries before marking any proposal as valid.
3. **In-Memory Domain Entity**: In Phase 4, `FixProposal` remains an in-memory Pydantic domain model without database table migrations.
4. **Passive Untrusted Boundary**: CI logs and source code comments are treated strictly as passive data.

## Consequences
### Positive
* Complete explainability: Every patch is parsed, measured, and verified against grounded evidence.
* Safe by default: Malicious or unverified paths are rejected before reaching execution or human review.
* Clear risk tagging: Dependency changes are automatically flagged as elevated risk.

### Negative / Limitations
* In V1, automated patch synthesis is restricted to at most 2 files and 100 lines. Multi-file architectural refactorings are out of scope.
