# Local Development Environment Setup: Akesis V1

```text
Status: Authoritative V1 Local Development Guide
Runtime: Python 3.12+ / uv / Docker / PostgreSQL 16
```

---

## 1. Prerequisites

Verify that the following development tools are installed on your host workstation:

* **Operating System:** Linux (Ubuntu 22.04+), macOS (Sonoma+), or Windows 11 with WSL2 / PowerShell.
* **Python:** Version 3.12 or higher (`python --version`).
* **Package Manager:** **`uv`** (>= 0.4.0) — *(Poetry is not supported in this repository)*.
* **Container Runtime:** Docker Desktop or Docker Engine (>= 24.0) with daemon running.
* **Git:** Version 2.40 or higher.

---

## 2. Step-by-Step Local Setup

### Step 1: Clone Repository
```bash
git clone https://github.com/crlabs-ai/akesis.git
cd akesis
```

### Step 2: Synchronize Virtual Environment
```bash
# Sync Python 3.12 virtual environment and all dependencies via uv
uv sync
```

### Step 3: Configure Environment Variables
```bash
# Create local .env from example template
cp .env.example .env
```

Edit `.env` and set your credentials:
```ini
ENVIRONMENT=development
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://postgres:postgrespassword@localhost:5436/akesis
GITHUB_WEBHOOK_SECRET=test_webhook_secret
GITHUB_TOKEN=your_github_token_here
GEMINI_API_KEY=your_gemini_api_key_here
SLACK_SIGNING_SECRET=test_slack_signing_secret
```

### Step 4: Start PostgreSQL 16 Database
```bash
# Start local PostgreSQL container exposed on port 5436
docker compose -f docker/docker-compose.yml up -d postgres
```

Verify database container is running:
```bash
docker ps --filter "name=akesis-postgres"
```

### Step 5: Apply Database Migrations
```bash
# Apply Alembic async migrations to create approvals and mutations tables
uv run alembic upgrade head
```

### Step 6: Build the Docker Validator Image
```bash
# Build local sandbox image used for container validation
docker build -f docker/Dockerfile.validator -t akesis-validator:v1 .
```

### Step 7: Launch Local FastAPI Server
```bash
# Start local Uvicorn development server with hot reload
uv run uvicorn src.apps.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Verify server status:
```bash
curl http://127.0.0.1:8000/health/liveness
# Response: {"status":"ok"}
```

---

## 3. Canonical Quality Gate Commands

Always run these verification commands before submitting a pull request:

```bash
# 1. Run unit test suite (deterministic, offline, no Docker/DB required)
uv run pytest tests/unit

# 2. Run full test suite (including database and integration tests)
uv run pytest tests/

# 3. Run test coverage analysis
uv run pytest --cov=src --cov-report=term-missing tests/unit

# 4. Run Ruff static linter
uv run ruff check .

# 5. Run Ruff formatter check
uv run ruff format --check .

# 6. Run strict Mypy type checking
uv run mypy src
```

---

## 4. Troubleshooting Local Development

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| `ConnectionRefusedError: [WinError 1225]` or `localhost:5436 connection refused` | PostgreSQL container is stopped | Run `docker compose -f docker/docker-compose.yml up -d postgres` |
| `DockerUnavailableError: Docker daemon not running` | Docker service is not running | Start Docker Desktop or run `sudo systemctl start docker` |
| `LLMAuthError: Gemini API key is not configured` | Missing `GEMINI_API_KEY` in `.env` | Add valid key from Google AI Studio to `.env` |
| `Alembic Target database is not up to date` | Pending database migrations | Run `uv run alembic upgrade head` |
