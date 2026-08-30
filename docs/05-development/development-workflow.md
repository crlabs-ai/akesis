# Development Workflow & Engineering Lifecycle

---

## The 10-Step Development Loop

```
[1. Understand Task] ──> [2. Read Docs & ADRs] ──> [3. Inspect Existing Code] ──> [4. Formulate Plan] ──> [5. Implement]
                                                                                                               │
[10. Open PR] <── [9. Conventional Commit] <── [8. Review Diff] <── [7. Quality Gates] <── [6. Write Tests] <──┘
```

1. **Understand Task:** Review requirements, scope, and acceptance criteria.
2. **Read Docs & ADRs:** Check `docs/` and `docs/06-decisions/` for architectural constraints.
3. **Inspect Existing Code:** Review existing modules and test fixtures.
4. **Formulate Plan:** Plan minimal necessary changes within V1 scope.
5. **Implement:** Write clean, modular Python 3.12+ code with full type annotations.
6. **Write Tests:** Add unit/integration tests with `pytest` (maintain coverage $\ge 85\%$).
7. **Quality Gates:** Run `uv run ruff check .`, `uv run ruff format .`, `uv run mypy .`.
8. **Review Diff:** Run `git diff` to verify zero unintended changes.
9. **Conventional Commit:** Create an atomic commit following Conventional Commits 1.0.0.
10. **Open PR:** Submit Pull Request with clear summary.
