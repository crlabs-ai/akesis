# Deployment Architecture: Akesis V1

---

## 1. V1 Deployment Model
Akesis V1 deploys as a straightforward containerized service:

```text
[Internet]
    │
    ▼
[Reverse Proxy / Ingress (TLS 1.3)]
    │
    ▼
[Akesis Application Service (FastAPI / Python 3.12+)]
    │
    ├──> [Structured Logs (structlog)]
    └──> [Local Docker Daemon (Ephemeral Sandboxes)]
```

* **Simplicity:** Single container deployment containing the FastAPI gateway, diagnostic agent, and Docker runner.
* **Scalability Path:** For distributed worker pools, Redis queues, and Kubernetes orchestration, refer to [`docs/future-scaling.md`](../future-scaling.md).
