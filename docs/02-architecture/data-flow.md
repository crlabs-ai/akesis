# Data Flow & Privacy Architecture: Akesis V1

---

## 1. End-to-End Data Movement

| Stage | Input Data | Process / Transformation | Output Data | Security Level |
| :--- | :--- | :--- | :--- | :--- |
| **1. Webhook** | GitHub Webhook JSON | Verify HMAC-SHA256 signature; extract metadata. | `IncidentEvent` | Public Metadata |
| **2. Log Ingest** | Raw CI stdout/stderr | Strip ANSI codes; sanitize secrets (`API_KEY`, `TOKEN`). | `SanitizedLog` | Internal Sensitive |
| **3. Context** | Target source files | Extract relevant lines surrounding traceback. | `CodeContext` | Customer Confidential |
| **4. Inference** | Log error + Code context | Prompt primary LLM provider over TLS 1.3. | `StructuredPatch` | Customer Confidential |
| **5. Sandbox** | Unified diff + Docker image | Execute build command in isolated container. | `ValidationLog` | Internal Diagnostic |
| **6. Delivery** | Validated diff + metadata | GitHub API call to create branch and Pull Request. | GitHub PR | Customer Repository |

---

## 2. Customer Code Privacy Guarantees
* **Zero Permanent Retention:** Source code files are processed ephemerally in memory or temporary directories and deleted upon incident completion.
* **No Model Training:** API integrations use commercial enterprise terms ensuring customer code is not used for foundation model training.
* **Secrets Masking:** Logs are filtered through regex secret scrubbers before being sent to the LLM interface.
