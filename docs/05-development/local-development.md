# Local Development Environment Setup: Akesis

This document details the workstation setup, tools, and commands required to develop Akesis locally.

---

## 1. Prerequisites
*   **Operating System:** Linux (Ubuntu 22.04+ / Debian 12+) or macOS (Sonoma+).
*   **Python:** Version 3.12 or higher.
*   **Package Manager:** `poetry` (>= 1.8.0) or `uv`.
*   **Container Runtime:** Docker Engine (>= 24.0) with Docker Compose v2.
*   **Git:** Version 2.40+ configured with SSH keys.

---

## 2. Setup Commands

```bash
# 1. Clone repository
git clone git@github-crlabs:crlabs-ai/akesis.git
cd akesis

# 2. Configure Python Virtual Environment
poetry install --with dev

# 3. Setup local environment variables
cp .env.example .env
```

---

## 3. Standard Verification Commands

```bash
# Run automated test suite
poetry run pytest tests/ -v

# Run test coverage report
poetry run pytest --cov=src --cov-report=term-missing tests/

# Run code formatter
poetry run black src/ tests/

# Run linter
poetry run ruff check src/ tests/

# Run strict type checking
poetry run mypy --strict src/
```
