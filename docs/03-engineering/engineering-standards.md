# Engineering Standards: Akesis V1

---

### 1. Canonical Tooling & Runtime
* **Python Runtime:** **Python 3.12+**
* **Package & Environment Manager:** **`uv`** (all dependency management and tool invocation uses `uv`).

### 2. Type Safety & Strict Verification
All Python code must include complete type annotations and pass `mypy --strict` with zero errors.

### 3. Structured Logging (structlog)
V1 uses `structlog` for structured JSON logging. Every log entry must include contextual correlation IDs (`incident_id`, `repo_id`, `workflow_run_id`). (OpenTelemetry is reserved for future production scaling).

### 4. Design for Failure
Implement timeouts on external GitHub API and LLM provider calls. Never allow unhandled exceptions to crash the application.

### 5. Modularity & Clean Boundaries
* `src/apps/`: Interface adapters (FastAPI, CLI).
* `src/packages/`: Pure domain logic and models.
