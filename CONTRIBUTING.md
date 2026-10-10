# Contributing to Akesis

Thank you for contributing to **Akesis**! This document outlines the engineering guidelines, local development workflow, and code quality expectations for all contributors.

```text
Ecosystem: CRLabs AI
Repository: crlabs-ai/akesis
Workflow: Trunk-Based Development with Short-Lived Feature Branches
```

---

## 1. Local Workstation Setup

### Prerequisites
* **Python:** Version 3.12 or higher.
* **`uv`:** Version 0.4.0 or higher.
* **Docker Engine:** Version 24.0 or higher with running daemon.
* **PostgreSQL:** Version 16 (or run via Docker Compose).

### Setup Commands
```bash
# 1. Clone repository
git clone https://github.com/crlabs-ai/akesis.git
cd akesis

# 2. Synchronize virtual environment with uv
uv sync

# 3. Configure local environment variables
cp .env.example .env

# 4. Start local PostgreSQL container
docker compose -f docker/docker-compose.yml up -d postgres

# 5. Apply database migrations
uv run alembic upgrade head

# 6. Build the sandbox validator image
docker build -f docker/Dockerfile.validator -t akesis-validator:v1 .
```

---

## 2. Quality Gate Verification

Before opening a pull request, ensure all canonical checks pass locally:

```bash
# Run unit test suite
uv run pytest tests/unit

# Run full test suite (with PostgreSQL container running)
uv run pytest tests/

# Run Ruff linter
uv run ruff check .

# Run Ruff formatter check
uv run ruff format --check .

# Run strict Mypy type checker
uv run mypy src
```

---

## 3. Development Workflow

We follow trunk-based development with short-lived feature branches:

1. **Create Feature Branch:** Branch from `main` using descriptive naming:
   `feat/<ticket-or-name>`, `fix/<ticket-or-name>`, or `docs/<ticket-or-name>`.
2. **Implement & Test:** Write clean, minimal, and fully tested Python 3.12 code conforming to repository standards.
3. **Commit Messages:** Follow the Conventional Commits 1.0.0 specification:
   ```text
   <type>(<scope>): <description>

   [optional body]
   ```
   *Approved types:* `feat`, `fix`, `docs`, `refactor`, `chore`, `test`, `style`, `ci`, `perf`.
4. **Self-Review:** Review your own diff before requesting review. Ensure no secrets or unnecessary files are staged.
5. **Open Pull Request:** Submit a Pull Request against `main`. All CI checks must pass before peer review and squash-merging.

---

## 4. Documentation & Standards Reference

* [Project Charter](docs/00-foundation/project-charter.md)
* [Coding Standards](docs/03-engineering/coding-standards.md)
* [Testing Strategy](docs/03-engineering/testing-strategy.md)
* [High-Level Design](docs/architecture/HLD.md)
* [Low-Level Design](docs/architecture/LLD.md)
