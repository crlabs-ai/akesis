# Testing Strategy: Akesis V1

---

## 1. Testing Pyramid
* **Unit Tests (60%):** Test log parsing, diff formatting, Pydantic schema validation, and confidence calculations using `pytest`. Fast, deterministic, zero external calls.
* **Integration Tests (30%):** Test Docker sandbox lifecycle and local API endpoints.
* **End-to-End Tests (10%):** Mock GitHub webhook ingestion through to mock PR generation.

---

## 2. Execution & Coverage
* **Command:** `uv run pytest tests/`
* **Coverage Baseline:** Minimum **85% statement coverage** enforced by `pytest-cov`.
* **Mocking Policy:** Mock all external HTTP calls (GitHub API, LLM providers) using `respx` or `pytest-mock`.
