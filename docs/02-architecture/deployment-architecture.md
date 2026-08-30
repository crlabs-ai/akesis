# Deployment Architecture: Akesis

---

## 1. V1 Deployment Model (Target Baseline)
For the V1 release, Akesis deploys as containerized services managed via Docker Compose or AWS ECS:

```text
[Internet]
    │
    ▼
[AWS ALB / Cloudflare Ingress (TLS 1.3)]
    │
    ├──> [FastAPI Ingestion Gateway (2 Replicas)]
    │         │
    │         ▼
    │    [Redis Cluster (Event Queue & State Cache)]
    │         ▲
    │         │
    └──> [Orchestration & Agent Workers (3 Replicas)]
              │
              ├──> [PostgreSQL 16 (RDS / Managed DB)]
              └──> [Dedicated Sandbox Worker Node (Docker Engine Pool)]
```

---

## 2. Future Production Scaling (Post-V1)
*   **Kubernetes Orchestration:** Migrate worker pools to Amazon EKS / GKE with Horizontal Pod Autoscaling (HPA) triggered by Redis queue depth.
*   **MicroVM Sandboxes:** Transition from Docker containers to Firecracker / gVisor microVMs for sub-second boot times and hardware-level isolation.
*   **Multi-Region Webhook Edge:** Deploy Ingestion gateways at edge locations to minimize webhook receipt latency.
