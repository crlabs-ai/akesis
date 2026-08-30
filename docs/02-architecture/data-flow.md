# Data Flow & Privacy Architecture: Akesis

This document details how data moves through Akesis, identifying data transformations, sensitive boundaries, and privacy protections.

---

## 1. End-to-End Data Movement

| Stage | Input Data | Transformation / Process | Output Data | Security Classification |
| :--- | :--- | :--- | :--- | :--- |
| **1. Webhook** | GitHub Webhook JSON | Verify HMAC-SHA256 signature; extract metadata. | `IncidentEvent` | Public Metadata |
| **2. Log Ingest** | Raw CI stdout/stderr | Strip ANSI codes; sanitize secrets (`API_KEY`, `TOKEN`). | `SanitizedLog` | Internal Sensitive |
| **3. Context** | Target source files | Tree-sitter AST extraction of failing function/block. | `CodeContext` | Customer Confidential |
| **4. Inference** | Log error + Code AST | Model prompt evaluation via TLS 1.3 to LLM provider. | `StructuredPatch` | Customer Confidential |
| **5. Sandbox** | Unified diff + Docker image | Ephemeral container execution of build command. | `ValidationLog` | Internal Diagnostic |
| **6. Delivery** | Validated diff + metadata | GitHub API call to create branch and Pull Request. | GitHub PR | Customer Repository |

---

## 2. Customer Code Privacy Guarantees
*   **Zero Permanent Storage:** Customer source code files are fetched ephemerally into memory or temporary RAM-disks during analysis and discarded upon incident completion.
*   **No Model Training:** Model provider agreements must strictly stipulate that data transmitted via API endpoints is never used for foundation model training.
*   **Secrets Masking:** All log streams pass through an automated regular-expression secret scrubber prior to entering the agent reasoning pipeline.
