# Local Development Environment Setup: Akesis V1

---

## 1. Prerequisites
* **Operating System:** Linux (Ubuntu 22.04+ / Debian 12+) or macOS (Sonoma+).
* **Python:** Version 3.12 or higher.
* **Package Manager:** **`uv`** (>= 0.4.0). *(Poetry is not supported).*
* **Container Runtime:** Docker Engine (>= 24.0).
* **Git:** Version 2.40+ configured with SSH keys.

---

## 2. Setup Commands

```bash
# 1. Clone repository
git clone git@github-crlabs:crlabs-ai/akesis.git
cd akesis

# 2. Sync virtual environment with uv
uv sync

# 3. Setup local environment variables
cp .env.example .env
```

---

## 3. Canonical Quality Gate Commands

```bash
# Run automated test suite
uv run pytest tests/ -v

# Run test coverage report
uv run pytest --cov=src --cov-report=term-missing tests/

# Run code formatter check
uv run ruff format --check .

# Run linter
uv run ruff check .

# Run strict type checking
uv run mypy .
```
