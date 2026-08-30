# Definition of Done (DoD): Akesis V1

A task or pull request is **DONE** only when all applicable criteria are satisfied:

---

### 1. Code & Architecture
- [ ] Meets functional requirements within the V1 scope boundary.
- [ ] Introduces zero unapproved dependencies or speculative infrastructure.
- [ ] All public functions, classes, and modules include docstrings.

### 2. Static Analysis & Type Checking
- [ ] Code formatted with `uv run ruff format .` (or black).
- [ ] Linter passes with 0 errors via `uv run ruff check .`.
- [ ] Type checker passes with 0 errors via `uv run mypy .`.

### 3. Testing & Verification
- [ ] Unit/integration tests cover all new logic.
- [ ] Statement coverage remains $\ge 85\%$ via `uv run pytest --cov`.
- [ ] All tests pass locally and in CI.

### 4. Security & Privacy
- [ ] Zero secrets or credentials present in diffs.
- [ ] Sandbox non-root and network policy rules are preserved.

### 5. Documentation & Git Hygiene
- [ ] Relevant documentation under `docs/` is updated.
- [ ] Commit message complies with Conventional Commits 1.0.0.
- [ ] Git diff reviewed prior to PR submission.
