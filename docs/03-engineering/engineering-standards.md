# Engineering Standards & Principles: Akesis

Every software engineer and AI agent contributing to the Akesis codebase must strictly adhere to these standards.

---

### 1. Type Safety & Strict Verification
All Python code must include full type annotations. Code must pass `mypy --strict` with zero errors. Avoid `Any` types; use explicit Pydantic models or Generics.

### 2. Design for Failure
Assume external APIs, database connections, and LLM endpoints will fail. Implement explicit timeouts, retry policies with jitter, and circuit breakers. Never allow an unhandled exception to crash worker processes.

### 3. Modularity & Clean Boundaries
Maintain strict separation of concerns:
*   `src/apps/`: Interface adapters (FastAPI, CLI).
*   `src/packages/`: Pure domain logic with zero circular dependencies.
*   `src/infra/`: Declarative infrastructure specifications.

### 4. Structured Observability
Use structured JSON logging for all log events. Every log entry must include `incident_id`, `repo_id`, and standard OpenTelemetry correlation IDs (`trace_id`, `span_id`).

### 5. Deterministic Dependency Management
Use `poetry` or `uv` with checked-in lockfiles. Dependencies must be pinned to exact versions. Adding third-party packages requires explicit justification and architectural sign-off.
