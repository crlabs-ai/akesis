# Model Strategy: Akesis V1

---

## 1. V1 Model Architecture
Akesis V1 uses a direct, provider-agnostic interface calling a single primary configured model:

```
LLMClientProtocol (Interface) ──> GeminiClient (Adapter) ──> Structured Pydantic Output ──> DiagnosticService
```

* **V1 LLM Provider:** Google Gemini (default: `gemini-1.5-flash` / `gemini-1.5-pro`).
* **Provider Abstraction:** `LLMClientProtocol` in `src/packages/sdk/llm_client.py` decouples domain and service logic from specific vendor SDKs.
* **Current Capability:** Single-call structured diagnosis with evidence extraction, bounded confidence calculation, and schema validation.
* **Current Limitation:** Diagnosis only. No autonomous command execution, patch application, or PR creation.
* **Future Evolution:** Multi-node LangGraph orchestration, patch synthesis, and sandbox validation (Phase 3 & Phase 4).
