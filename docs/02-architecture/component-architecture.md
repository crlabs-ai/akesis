# Component Architecture: Akesis V1

This document details the internal modules, interfaces, and responsibilities across the Akesis V1 platform.

---

## 1. Ingestion Gateway (`src/apps/api`)
* **Responsibility:** Receives inbound webhooks from GitHub Actions (`POST /v1/webhooks/github`); validates HMAC-SHA256 signatures; normalizes event payloads into `IncidentEvent` domain models.
* **Technology:** Python 3.12, FastAPI, Uvicorn.
* **Observability:** Logs request lifecycle with correlation IDs using `structlog`.

---

## 2. Context & Log Extractor (`src/packages/shared`)
* **Responsibility:** Parses raw CI logs to extract error blocks, stack traces, file paths, and line numbers. Fetches relevant source file context from the repository without building a complex AST platform.
* **Strategy:** Traceback regex parsing + targeted line range extraction.

---

## 3. Diagnostic & Patch Agent (`src/packages/agent-runtime`)
* **Responsibility:** Implements a deterministic state machine that formats context, calls the configured LLM provider interface, validates Pydantic JSON output, and constructs minimal unified diffs.
* **Technology:** Pydantic V2, LiteLLM / direct client interface.

---

## 4. Sandbox Execution Engine (`src/packages/shared` / Docker)
* **Responsibility:** Manages ephemeral Docker containers; applies unified diffs; executes build/lint/test commands; asserts exit code 0; enforces non-root execution and default network isolation.
* **Technology:** `docker-py` / Docker Engine API.

---

## 5. PR Delivery Service (`src/packages/sdk`)
* **Responsibility:** Communicates with GitHub REST API using GitHub App installation tokens; creates fix branches; commits validated diffs; opens Pull Requests with diagnostic evidence.
