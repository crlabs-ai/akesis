# System Architecture: Akesis V1

```text
Status: Approved V1 Baseline (Vertical Slice)
Architecture Style: Modular Monolith / Single-Service Control Plane
Tooling: Python 3.12+ / uv
Logging: structlog with Correlation IDs
```

---

## 1. High-Level System Landscape

Akesis V1 is architected as a complete, robust vertical slice centered around a direct pipeline:

```
Event Ingestion ──> Context Collection ──> Diagnosis ──> Remediation ──> Validation ──> Human Decision ──> Resolution
```

```mermaid
graph TD
    subgraph "External Systems"
        GH[GitHub / GitHub Actions]
        LLM[Configured LLM Provider<br/>OpenAI / Anthropic / Gemini]
    end

    subgraph "Akesis V1 Service (FastAPI / Python 3.12)"
        Gateway[Webhook Ingestion Gateway]
        ContextEngine[Context & Log Extractor]
        AgentEngine[Diagnostic Agent Runtime]
        DeliveryEngine[GitHub PR Delivery Service]
        Logger[Structured Logger<br/>structlog + Correlation IDs]
    end

    subgraph "Local Isolation Boundary"
        DockerBox[Ephemeral Docker Sandbox<br/>Non-Root / Resource-Capped]
    end

    GH -->|1. Webhook: workflow_run| Gateway
    Gateway -->|2. Log Event| Logger
    Gateway -->|3. Fetch Trace & Files| ContextEngine
    ContextEngine -->|4. Extract Signals| AgentEngine
    AgentEngine -->|5. Structured Prompt / Schema| LLM
    LLM -->|6. Return Unified Diff| AgentEngine
    AgentEngine -->|7. Execute Patch & Verify| DockerBox
    DockerBox -->|8. Report Exit Code| AgentEngine
    AgentEngine -->|9. Open PR on Pass| DeliveryEngine
    DeliveryEngine -->|10. Submit PR with Evidence| GH
```

---

## 2. V1 Architectural Decisions

1. **Modular Single Service:** The ingestion gateway, context extractor, agent coordinator, and delivery engine run as a clean, modular Python application. No distributed task queues or microservices are required for V1.
2. **Provider-Agnostic LLM Interface:** A clean abstraction layer wraps the primary configured model (e.g. Claude 3.5 Sonnet or GPT-4o) using Pydantic schemas. No multi-tier routing or dynamic model selection agents in V1.
3. **Structured Contextual Logging:** All components log via `structlog` with correlation IDs (`incident_id`, `repo_id`, `run_id`).
4. **Pragmatic Docker Sandboxing:** Ephemeral container spawned on the host Docker daemon. Network access is disabled by default, enabled only during dependency installation steps.
