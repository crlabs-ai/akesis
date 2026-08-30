# Definition of Done (DoD): Akesis

A task, feature, or pull request is considered **DONE** and ready for production merge only when all applicable criteria below are satisfied:

---

### 1. Code & Architecture Quality
- [ ] Implementation satisfies all specified functional requirements.
- [ ] No speculative or unrequested features have been introduced.
- [ ] Code complies with modularity standards (no circular imports; clear package boundaries).
- [ ] All public functions, classes, and modules have descriptive docstrings.

### 2. Static Analysis & Type Checking
- [ ] Formatting strictly adheres to `black` standards.
- [ ] Linter checks pass with 0 errors via `ruff`.
- [ ] Type checker passes with 0 errors via `mypy --strict`.

### 3. Testing & Verification
- [ ] Unit tests cover all new business logic.
- [ ] Integration tests verify database/container integrations where applicable.
- [ ] Overall test coverage remains $\ge 85\%$.
- [ ] All tests execute and pass in the automated CI pipeline.

### 4. Security & Privacy
- [ ] No secrets, keys, or internal tokens are present in code or commit diffs.
- [ ] Sandbox boundaries and isolation policies are preserved.
- [ ] Prompt templates include injection mitigation boundaries.

### 5. Documentation & Git Hygiene
- [ ] Relevant documentation under `docs/` is updated to reflect changes.
- [ ] Git commit message complies with Conventional Commits 1.0.0.
- [ ] Pull request diff has been self-reviewed prior to requesting peer review.
