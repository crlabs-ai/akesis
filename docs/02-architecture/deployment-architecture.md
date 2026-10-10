# Deployment Architecture: Akesis V1

```text
Status: Approved V1 Authoritative Architecture
Deployment Model: Single-Host Modular Monolith
Persistence: PostgreSQL 16 Database
Isolation: Ephemeral Docker Validator Containers
```

---

## 1. Single-Host Deployment Model

Akesis V1 deploys as a cohesive, single-host control-plane service designed to be operated and maintained by a single engineer:

```text
[Public Internet]
       │
       ▼ (HTTPS / TLS 1.3)
[Reverse Proxy / Ingress (Nginx / Caddy / ALB)]
       │
       ▼ (HTTP 127.0.0.1:8000)
[Akesis Application Service (FastAPI / Uvicorn)]
       │
       ├──► [PostgreSQL 16 Database (Port 5432/5436)]
       │    └── approvals & mutations tables
       │
       ├──► [Local Docker Daemon (/var/run/docker.sock)]
       │    └── akesis-validator:v1 ephemeral containers
       │
       └──► [Outbound HTTPS Integrations (TLS 1.3)]
            ├── Google Gemini REST API
            ├── GitHub REST API
            └── Slack Incoming Webhooks & response_url
```

---

## 2. Infrastructure Components

1. **Reverse Proxy (Nginx / Caddy):** Terminates TLS 1.3, manages SSL certificates, passes `X-Request-ID` correlation headers, and routes `/v1/webhooks/github` and `/v1/slack/interactions` to the backend.
2. **FastAPI Application Process:** Runs under Python 3.12 managed by `systemd` or a container supervisor. Handles asynchronous request parsing, domain workflows, and background tasks.
3. **PostgreSQL 16 Database:** Durable relational store for `approvals` and `mutations` state machines with transactional integrity.
4. **Host Docker Daemon:** Executes ephemeral `akesis-validator:v1` sandbox containers with network isolation and resource limits.

---

## 3. Horizontal Scaling & Multi-Node Evolution

For enterprise distributed scaling requirements (Celery/Redis worker pools, Kubernetes orchestrators, multi-region container clusters), refer to [`docs/future-scaling.md`](../future-scaling.md).
