# Engineering Phase Plan: Akesis V1 Roadmap

---

## Phase 0: Project Foundation & Architecture (Current / Frozen)
* [x] Establish authoritative documentation hierarchy (`docs/00` through `docs/06`).
* [x] Define AGENTS.md operational manual and source-of-truth hierarchy.
* [x] Publish Project Charter, PRD, System Architecture, and ADR-0001.
* [x] Freeze V1 scope and document future production scaling roadmap.

---

## Phase 1: Ingestion & Log Parsing Core
* [ ] Build FastAPI Ingestion Gateway (`/v1/webhooks/github`) with HMAC validation.
* [ ] Implement Log Sanitizer and ANSI strip parser.
* [ ] Implement focused code-context extractor for traceback lines.
* [ ] Unit test suite for parsers using `uv run pytest`.

---

## Phase 2: Docker Sandbox & Validation Engine
* [ ] Implement Docker Engine lifecycle manager using `docker-py`.
* [ ] Configure unprivileged container profiles (non-root, 512MB RAM, 1 CPU).
* [ ] Implement network policy (disabled by default; enabled for dependency installs).
* [ ] Integration test suite for sandbox execution.

---

## Phase 3: Agent Orchestration & Patch Synthesis
* [ ] Implement Pydantic structured output models for diagnostic schemas.
* [ ] Build deterministic state machine coordinator.
* [ ] Integrate provider-agnostic LLM interface.
* [ ] 10–12 scenario evaluation benchmark suite.

---

## Phase 4: GitHub App Integration & PR Delivery
* [ ] Implement GitHub App authentication and ephemeral token exchange.
* [ ] Build Pull Request delivery service with rich markdown trace formatting.
* [ ] End-to-end integration tests with mock GitHub API.

---

## Phase 5: Verification & Vertical Slice Polish
* [ ] Structured logging verification (`structlog`).
* [ ] Full vertical slice validation across all 3 V1 remediation categories.
* [ ] Final review before release.
