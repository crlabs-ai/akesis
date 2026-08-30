# Model Strategy: Akesis V1

---

## 1. V1 Model Architecture
Akesis V1 uses a direct, provider-agnostic interface calling a single primary configured model:

```
LLM Provider Interface ──> Primary Configured Model ──> Structured Pydantic Output ──> Application Validation
```

* **Primary Model Options:** Claude 3.5 Sonnet, GPT-4o, or Gemini 1.5 Pro (configured via environment variable).
* **Provider-Agnostic Design:** The abstraction layer allows swapping the underlying model without changing agent logic.
* **No Multi-Tier Routing in V1:** Intelligent multi-tier routing, dynamic model selection, and cost/latency optimizers are deferred to future production scaling (see [`docs/future-scaling.md`](../future-scaling.md)).
