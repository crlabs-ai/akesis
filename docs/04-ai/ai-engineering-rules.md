# AI Engineering Rules for Repository Development

These 15 rules apply to all AI assistants (Antigravity, OpenCode, Codex, Claude) contributing code or documentation to Akesis:

1.  **Read AGENTS.md First:** Always review `AGENTS.md` and relevant docs before generating code.
2.  **Inspect Existing Implementations:** Verify existing modules and patterns before introducing new files.
3.  **No Invented Requirements:** Implement only what is explicitly specified in `docs/01-product/`.
4.  **Preserve Architectural Decisions:** Strictly respect existing ADRs in `docs/06-decisions/`.
5.  **Smallest Correct Diff:** Make surgical, atomic edits. Do not touch unrelated files or lines.
6.  **No Unapproved Dependencies:** Do not add third-party libraries without explicit ADR approval.
7.  **Strict Typing:** Ensure every function and class has complete, strict type annotations.
8.  **Comprehensive Tests:** Accompany every feature with unit and integration tests.
9.  **Run Quality Gates:** Verify that code passes linters, formatters, and type checkers.
10. **Review Diffs:** Inspect the final git diff to ensure zero unintended changes.
11. **Update Documentation:** Keep `docs/` in sync whenever system interfaces change.
12. **Evidence-Based Assertions:** Never claim a benchmark or test passed without executing it.
13. **Zero Hardcoded Secrets:** Never insert fake API keys, real tokens, or passwords into code.
14. **Stop on Ambiguity:** When requirements or architecture conflict, stop and ask the user.
15. **Report Exact Changes:** Summarize modified files clearly and concisely upon completion.
