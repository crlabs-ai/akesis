# Git & Version Control Workflow

---

## 1. Branching Model: Trunk-Based Development
*   **Primary Branch:** `main` (Protected; direct pushes prohibited).
*   **Feature Branches:** Short-lived branches (< 72 hours) branched from `main`.
*   **Naming Convention:**
    *   `feat/<ticket-or-short-desc>`
    *   `fix/<ticket-or-short-desc>`
    *   `docs/<ticket-or-short-desc>`
    *   `chore/<ticket-or-short-desc>`

---

## 2. Commit Message Standard: Conventional Commits 1.0.0
All commit messages must follow the format:
```text
<type>(<scope>): <short summary>

[optional body explaining rationale]

[optional footer(s)]
```
*   **Allowed Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`.
*   **Example:** `feat(agent): implement AST context extractor for python files`

---

## 3. Pull Request Quality Gates
Before merging a Pull Request into `main`:
1.  All automated CI checks (lint, format, mypy, pytest) must pass green.
2.  PR size must ideally be $< 400$ lines of code.
3.  Squash-and-Merge is the mandatory merge strategy to maintain a clean linear history.
