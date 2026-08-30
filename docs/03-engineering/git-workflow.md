# Git & Version Control Workflow

---

## 1. Branching Model: Trunk-Based Development
* **Main Branch:** `main` (Protected; direct pushes prohibited).
* **Feature Branches:** Short-lived branches (< 72 hours) branching from `main`.
* **Naming:** `feat/<desc>`, `fix/<desc>`, `docs/<desc>`, `chore/<desc>`.

---

## 2. Conventional Commits 1.0.0
Format: `<type>(<scope>): <summary>`
* Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`.
* Example: `feat(sandbox): implement network toggle policy for dependency installs`

---

## 3. Pull Request Quality Gates
1. All automated checks (`uv run ruff check .`, `uv run mypy .`, `uv run pytest`) pass green.
2. Squash-and-merge is the standard merge strategy.
