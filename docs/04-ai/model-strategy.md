# Model Strategy & Evaluation: Akesis

---

## 1. Model Tiering & Allocation

| Tier | Function | Selected Model | Rationale |
| :--- | :--- | :--- | :--- |
| **Tier 1 (Reasoning & Patch Gen)** | Deep root cause analysis, AST reasoning, and patch synthesis. | **Claude 3.5 Sonnet / GPT-4o** | High reasoning capability, superior unified diff formatting, low hallucination rate. |
| **Tier 2 (Extraction & Triage)** | Fast log parsing, stack trace isolation, and classification. | **Gemini 1.5 Flash / Claude 3.5 Haiku** | Sub-second latency ($< 500\text{ms}$), low token cost, massive context window (1M+ tokens). |
| **Tier 3 (Local / Fast Fallback)** | Offline classification and local development testing. | **Llama 3.3 70B (Ollama / vLLM)** | Cost-effective self-hosted fallback for private enterprise deployments. |

---

## 2. Resilience & Fallback Strategy
*   **Primary $ightarrow$ Secondary Routing:** If Tier 1 model API times out ($> 15\text{s}$) or returns HTTP 5xx / 429, the gateway automatically falls back to secondary provider.
*   **Token Budgeting:** Prompts cap log contexts at 2,000 tokens of sanitized tail logs to ensure latency remains under 3 seconds per inference call.
