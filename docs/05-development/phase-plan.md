# Engineering Phase Plan: Akesis Roadmap

---

## Phase 0: Project Foundation & Architecture (Current)
*   [x] Establish authoritative documentation hierarchy (`docs/00` through `docs/06`).
*   [x] Formulate AGENTS.md operational manual for AI developers.
*   [x] Publish Project Charter, PRD, System Architecture, and ADR-0001.

---

## Phase 1: Ingestion & Parsing Core
*   [ ] Build FastAPI Ingestion Gateway (`/v1/webhooks/github`) with HMAC validation.
*   [ ] Implement Log Sanitizer and ANSI strip parser.
*   [ ] Build Tree-sitter AST Context Extractor for Python, TypeScript, and Go.
*   [ ] Construct initial Unit Test Suite for parsers.

---

## Phase 2: Docker Sandbox & Validation Engine
*   [ ] Implement Docker Engine lifecycle manager using `docker-py`.
*   [ ] Configure unprivileged container profiles with dropped capabilities and tmpfs.
*   [ ] Build git patch applicator and build-command runner.
*   [ ] Integrate `testcontainers-python` integration test suite.

---

## Phase 3: Agent Orchestration & Patch Synthesis
*   [ ] Implement Pydantic structured output models for diagnostic schemas.
*   [ ] Build LangGraph state machine coordinator.
*   [ ] Integrate LiteLLM / Instructor client with fallback routing.
*   [ ] Establish golden evaluation benchmark test suite (200 cases).

---

## Phase 4: GitHub App Integration & PR Delivery
*   [ ] Implement GitHub App JWT auth and ephemeral token exchange.
*   [ ] Build Pull Request delivery service with rich markdown trace formatting.
*   [ ] End-to-end integration tests with mock GitHub API.

---

## Phase 5: Hardening, Observability & Beta Release
*   [ ] Configure OpenTelemetry tracing and Prometheus metrics.
*   [ ] Complete security penetration testing and sandbox boundary audit.
*   [ ] Deploy private beta to selected CRLabs AI partner repositories.
