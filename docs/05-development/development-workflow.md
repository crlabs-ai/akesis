# Development Workflow & Engineering Lifecycle

This document outlines the step-by-step workflow for contributors implementing features or bug fixes in Akesis.

---

## The 10-Step Development Loop

```
[1. Understand Task] ──> [2. Read Docs & ADRs] ──> [3. Inspect Existing Code] ──> [4. Formulate Plan] ──> [5. Implement]
                                                                                                               │
[10. Open PR] <── [9. Conventional Commit] <── [8. Review Diff] <── [7. Quality Gates] <── [6. Write Tests] <──┘
```

1.  **Understand Task:** Review requirements, user personas, and acceptance criteria.
2.  **Read Docs & ADRs:** Check `docs/` and `docs/06-decisions/` for relevant architectural constraints.
3.  **Inspect Existing Code:** Check existing implementations, interfaces, and test fixtures.
4.  **Formulate Plan:** Plan the minimal necessary changes. Avoid speculative additions.
5.  **Implement:** Write clean, modular Python 3.12+ code with complete type annotations.
6.  **Write Tests:** Add unit and integration tests with pytest to maintain coverage $\ge 85\%$.
7.  **Quality Gates:** Run local linters and type checkers (`black`, `ruff`, `mypy`).
8.  **Review Diff:** Run `git diff` to verify only intended files and lines were modified.
9.  **Conventional Commit:** Create an atomic commit following Conventional Commits 1.0.0.
10. **Open PR:** Submit Pull Request with a clear summary linking relevant issue tickets.
