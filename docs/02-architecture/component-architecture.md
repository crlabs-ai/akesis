# Component Architecture: Akesis V1

This document details the internal modules, interfaces, and responsibilities across the Akesis V1 platform.

```text
Status: Approved V1 Authoritative Architecture
Architecture Style: Modular Monolith
Persistence: PostgreSQL 16 (Durable Approval & Mutation State)
Tooling: Python 3.12+ / uv
Logging: structlog with Correlation IDs
```

---

## 1. Ingestion Gateway (`src/apps/api`)
* **Responsibility:** Receives inbound webhooks from GitHub Actions (`POST /v1/webhooks/github`) and interactive callbacks from Slack (`POST /v1/slack/interactions`).
* **Security:** Verifies GitHub `X-Hub-Signature-256` and Slack `X-Slack-Signature` HMAC-SHA256 signatures with a 300-second timestamp replay window.
* **Technology:** Python 3.12, FastAPI, Uvicorn.
* **Observability:** Logs request lifecycle with unique correlation IDs (`X-Request-ID`) using `structlog`.

---

## 2. Signal Extraction & Log Parser (`src/packages/shared/log_parser.py`)
* **Responsibility:** Strips ANSI formatting and extracts deterministic error signals from raw console logs.
* **Error Matchers:** Python Tracebacks, Pytest failures (`FAILED test.py::test_fn`), Ruff lint violations, Mypy type errors, and Pip/ModuleNotFoundError patterns.
* **Output:** Strongly typed [`FailureSignal`](../../src/packages/shared/models.py) and [`FailureContext`](../../src/packages/shared/models.py).

---

## 3. Codebase Context Retrieval (`src/packages/shared/context_resolver.py`)
* **Responsibility:** Clones or updates the target repository and checks out the exact failing commit SHA in detached HEAD mode via [`GitRepositoryCheckoutManager`](../../src/packages/sdk/repo_checkout.py).
* **Security:** Enforces path validation (`is_path_safe_and_within_root`) to prevent directory traversal escapes.
* **Budget Limits:** Extracts bounded line windows (default 40 lines) with character budget limits (max 8,000 characters total across candidate files).

---

## 4. Structured AI Diagnostic Engine (`src/packages/shared/diagnostic_service.py`)
* **Responsibility:** Formulates evidence-grounded prompts and invokes the Google Gemini REST API via [`GeminiClient`](../../src/packages/sdk/llm_client.py) (`gemini-1.5-flash`).
* **Safety:** Treats source code and logs as untrusted passive data; enforces schema-constrained JSON matching [`DiagnosisProposal`](../../src/packages/shared/models.py).
* **Fallback:** Returns a structured fallback diagnostic result with confidence 0.0 if the LLM provider fails or times out.

---

## 5. Fix Proposal Engine & Patch Validator (`src/packages/shared/fix_service.py` & `patch_validator.py`)
* **Responsibility:** Prompts Gemini for candidate unified diff patches and parses them into structured [`FilePatch`](../../src/packages/shared/models.py) objects.
* **Validation:** Enforces strict budgets (max 2 files, 100 lines, 4,000 characters), validates grounding against failure evidence, and blocks protected security paths (`.github/workflows/`, `.git/`, `.env*`).

---

## 6. Isolated Docker Sandbox Engine (`src/packages/shared/validation_service.py` & `src/packages/sdk/sandbox_runner.py`)
* **Responsibility:** Creates an ephemeral workspace on host, applies unified diff verbatim (`git apply --check` $\rightarrow$ `git apply` strictly without `--whitespace=fix`), and runs allowlisted verification commands (`pytest`, `ruff`, `mypy`, `python3 -m compileall`).
* **Containment:** Dedicated Docker image `akesis-validator:v1` executed with `--network none`, `--cap-drop ALL`, `--security-opt no-new-privileges`, and non-root user `akesis` (`uid 1000`).

---

## 7. Human Approval Gate & Slack Client (`src/packages/shared/approval_service.py` & `src/packages/sdk/slack_client.py`)
* **Responsibility:** Evaluates deterministic eligibility (passing validation, confidence $\ge 0.60$), creates pending approval records, and dispatches interactive Block Kit cards to Slack.
* **Persistence:** Commits atomic state transitions in PostgreSQL table `approvals` via [`ApprovalRepository`](../../src/packages/database/repositories.py).

---

## 8. Controlled Git Mutation & PR Delivery (`src/packages/shared/mutation_service.py` & `src/packages/sdk/github_client.py`)
* **Responsibility:** Verifies exact commit SHA consistency, creates dedicated fix branch (`akesis/fix/<incident>/<proposal>`), applies patch, executes pre-push sandbox validation, pushes branch, and opens a GitHub Pull Request with complete audit metadata.
* **Persistence:** Records full mutation lifecycle in PostgreSQL table `mutations` via [`MutationRepository`](../../src/packages/database/repositories.py).
