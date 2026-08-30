# Success Metrics & Target KPIs: Akesis V1

This document defines the quantitative targets and measurement methodologies for evaluating Akesis.

---

## 1. Quantitative Engineering Metrics

| Metric Name | Target | Measurement Method | Operational Value |
| :--- | :--- | :--- | :--- |
| **Patch Acceptance Rate (PAR)** | $> 85\%$ | $\frac{\text{Merged Akesis PRs}}{\text{Total Delivered Akesis PRs}} \times 100$ | Measures developer trust and code precision. |
| **Mean Time to Remediate (MTTR)** | $< 5\text{ min}$ | Timestamp of Webhook receipt to timestamp of PR creation. | Measures speed and elimination of context switching. |
| **Sandbox Validation Rate** | $100\%$ | $\frac{\text{PRs with Verified 0 Exit Code}}{\text{Total Delivered PRs}} \times 100$ | Enforces zero broken code delivered to users. |
| **False Positive Diagnosis Rate** | $< 5\%$ | Percentage of diagnoses rejected by developer as incorrect root cause. | Evaluates diagnostic reasoning quality. |
| **Sandbox Execution Overhead** | $< 45\text{s}$ | Duration of container spawn, patch apply, and verification run. | Prevents pipeline queue congestion. |

---

## 2. Qualitative KPIs & Product Health
*   **Developer Cognitive Overhead:** Evaluated via developer survey measuring reduction in time spent investigating broken builds.
*   **Zero Noise Mandate:** Track instances where Akesis generated an unnecessary or spam PR. Target: 0 spam PRs per 1,000 runs.
