# System Architecture: Akesis

```text
Status: Approved Architectural Baseline
Security Boundary: Tier 1 Zero-Trust Container Isolation
```

---

## 1. High-Level System Landscape

Akesis is designed as a distributed, event-driven remediation platform.

```mermaid
graph TD
    subgraph "External Ecosystem"
        GH[GitHub / GitHub Actions]
        LLM[Frontier LLM Provider]
    end

    subgraph "Akesis Ingress & Control Plane"
        Gateway[API Ingestion Gateway<br/>FastAPI / TLS 1.3]
        Queue[(Redis Event Queue<br/>BullMQ / Celery)]
        Orch[Orchestration Engine<br/>Python State Machine]
        DB[(PostgreSQL 16<br/>Audit & State Store)]
    end

    subgraph "Akesis Execution & AI Plane"
        Agent[Diagnostic Agent Runtime<br/>LangGraph / Structured JSON]
        Context[Context & AST Engine<br/>Tree-sitter / Log Parser]
        SandboxPool[Sandbox Container Pool<br/>Docker / gVisor Micro-Runtimes]
    end

    GH -->|1. Webhook Event| Gateway
    Gateway -->|2. Enqueue Incident| Queue
    Queue -->|3. Consume Task| Orch
    Orch -->|4. Store Event State| DB
    Orch -->|5. Fetch AST & Logs| Context
    Orch -->|6. Reason & Patch| Agent
    Agent -->|7. API Inference| LLM
    Agent -->|8. Dispatch Validation| SandboxPool
    SandboxPool -->|9. Return Compile Status| Orch
    Orch -->|10. Open PR with Scoped Token| GH
```

---

## 2. Trust & Security Boundaries

1.  **Ingress Boundary:** Public-facing API Gateway terminating TLS 1.3. Validates HMAC-SHA256 signatures before parsing webhooks.
2.  **Internal Control Plane:** Orchestration service, PostgreSQL database, and Redis queue reside in an isolated Virtual Private Cloud (VPC) with no public ingress.
3.  **Execution Sandbox Boundary (Strict Isolation):** Docker sandbox containers execute untrusted code in an unprivileged, capability-dropped runtime with dedicated CPU/memory limits and read-only host mounts.
4.  **Egress Boundary:** Outbound calls to GitHub API and LLM providers use scoped, ephemeral tokens with strict rate limiting.
