# Component Architecture: Akesis

This document details the internal modules, interfaces, and responsibilities across the Akesis platform.

---

## 1. Ingestion Gateway (`src/apps/api`)
*   **Responsibility:** Receives inbound webhooks from GitHub Actions; validates HMAC-SHA256 signatures; normalizes event payloads into `IncidentEvent` domain models; pushes tasks to the Redis queue.
*   **Technology:** Python 3.12, FastAPI, Uvicorn.
*   **Interfaces:** `POST /v1/webhooks/github`, `GET /health/liveness`, `GET /health/readiness`.

---

## 2. Orchestration & State Machine (`src/packages/agent-runtime`)
*   **Responsibility:** Manages the deterministic lifecycle of an incident across states (`INGESTED` $ightarrow$ `DIAGNOSING` $ightarrow$ `SYNTHESIZING` $ightarrow$ `VALIDATING` $ightarrow$ `DELIVERING` $ightarrow$ `COMPLETED` / `FAILED`).
*   **Technology:** Python state machine / LangGraph coordinator.

---

## 3. Diagnostic & Patch Agent (`src/packages/agent-runtime`)
*   **Responsibility:** Implements reasoning loops that parse sanitized logs, query syntax trees (ASTs), construct prompt schemas, invoke LLM providers, and generate structured unified diffs.
*   **Technology:** Pydantic structured output models, Instructor / LiteLLM abstraction.

---

## 4. Context & AST Engine (`src/packages/rag`)
*   **Responsibility:** Extracts targeted code snippets around failing lines using Tree-sitter AST parsing; analyzes dependency manifests (`package.json`, `pyproject.toml`, `go.mod`); strips ANSI codes from logs.

---

## 5. Sandbox Execution Engine (`src/packages/shared` / Docker)
*   **Responsibility:** Manages the lifecycle of ephemeral Docker containers; mounts the target codebase snapshot; applies unified diffs; executes build/lint/test commands; captures stdout/stderr and exit codes.
*   **Technology:** Docker Engine API / `docker-py`, gVisor runtime.

---

## 6. PR Delivery Service (`src/packages/sdk`)
*   **Responsibility:** Communicates with the GitHub REST API using GitHub App installation tokens; creates remote branches; commits validated diffs; opens formatted Pull Requests.
