# Future Production Scaling Roadmap (NOT V1)

```text
Status: Informational / Architectural Roadmap
Classification: FUTURE PRODUCTION SCALING / NOT IN V1 SCOPE
```

This document catalogs architectural patterns, infrastructure components, and platform enhancements that will become relevant as Akesis evolves into a distributed multi-tenant production platform. **None of these items are required or implemented in Akesis V1.**

---

## 1. Distributed Execution & Task Queues
* **Concept:** Asynchronous task queue (Redis with ARQ / Celery / BullMQ) distributing incident workloads across worker pools.
* **Why Not V1:** V1 operates as a single-service vertical slice with direct request execution. Distributed queues add infrastructure complexity before high-concurrency traffic justifies it.

## 2. OpenTelemetry & Distributed Tracing
* **Concept:** End-to-end distributed tracing across HTTP ingress, message queues, agent workers, and external API calls using OpenTelemetry SDKs and Jaeger/Tempo backends.
* **Why Not V1:** V1 uses `structlog` with correlation IDs (`incident_id`), which provides sufficient observability without the overhead of collector sidecars and trace export pipelines.

## 3. Kubernetes & Container Worker Pools
* **Concept:** Deploying worker pools to Amazon EKS / GKE with Horizontal Pod Autoscaling (HPA) and pre-warmed sandbox container pools.
* **Why Not V1:** V1 uses the local host Docker daemon to spin up containers on demand.

## 4. Advanced Sandbox Isolation (gVisor / Firecracker)
* **Concept:** Hardware-level microVM isolation using AWS Firecracker or gVisor runtimes for sub-second ephemeral VM lifecycles.
* **Why Not V1:** Standard Docker containers with non-root users, dropped capabilities, and default network disabling provide adequate isolation for V1.

## 5. Outbound Network Proxy Isolation
* **Concept:** Dedicated forward proxy service inspecting and filtering outbound HTTP/HTTPS traffic during dependency installation to prevent data exfiltration.
* **Why Not V1:** V1 uses a simple policy toggle (network disabled by default, enabled only during dependency installation steps).

## 6. Multi-Tier Model Routing & Selection Agents
* **Concept:** Intelligent meta-agents dynamically routing tasks between models based on real-time cost, latency, and reasoning complexity benchmarks.
* **Why Not V1:** V1 uses a clean provider-agnostic interface calling a single configured primary model.

## 7. Large-Scale Golden Evaluation Datasets (200+ Cases)
* **Concept:** Comprehensive 200+ case benchmark dataset covering dozens of languages, framework permutations, and historical failure traces.
* **Why Not V1:** V1 focuses on a focused, high-precision 10–12 scenario evaluation suite covering the three core V1 remediation capabilities.

## 8. Multi-Platform CI Support & Multi-Repo Management
* **Concept:** Native integration with GitLab CI/CD, CircleCI, Bitbucket Pipelines, Jenkins, and cross-repository dependency graphs.
* **Why Not V1:** V1 strictly targets GitHub Actions workflows.
