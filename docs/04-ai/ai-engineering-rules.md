# AI Engineering Rules for Repository Development

These 15 rules apply to all AI coding assistants (Antigravity, OpenCode, Codex, Claude) working in Akesis:

1. **Read AGENTS.md First:** Always review `AGENTS.md` and relevant docs before coding.
2. **Inspect Existing Implementations:** Verify existing modules before creating new files.
3. **No Invented Requirements:** Implement only what is explicitly defined in `docs/01-product/`.
4. **Preserve Architectural Decisions:** Strictly respect existing ADRs and V1 boundaries.
5. **Smallest Correct Diff:** Make surgical, atomic edits. Do not touch unrelated files.
6. **No Unapproved Dependencies:** Do not add third-party libraries without explicit approval.
7. **Use uv & Python 3.12+:** Use `uv run ...` for all tool execution and testing.
8. **Strict Typing:** Ensure every function and class has complete type annotations.
9. **Comprehensive Tests:** Accompany every feature with unit and integration tests.
10. **Run Quality Gates:** Verify code passes `ruff`, `mypy`, and `pytest`.
11. **Review Diffs:** Inspect git diffs to ensure zero unintended changes.
12. **Update Documentation:** Keep `docs/` in sync whenever interfaces change.
13. **Zero Hardcoded Secrets:** Never insert credentials into code.
14. **Stop on Ambiguity:** When requirements or architecture conflict, stop and ask.
15. **Report Exact Changes:** Summarize modified files clearly upon completion.
