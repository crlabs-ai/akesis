# Product Success Metrics

To validate the product performance of Akesis, we track quantitative and qualitative parameters.

---

## 1. Quantitative Product Metrics

| Metric Category | Metric Name | Target | Calculation Method |
| :--- | :--- | :--- | :--- |
| **Accuracy** | Patch Acceptance Rate | > 85% | Accepted PRs / Total PRs generated. |
| **Latency** | Mean Time to Repair (MTTR) | < 5 minutes | Log ingestion timestamp to PR creation. |
| **Quality** | Compilation Pass Rate | 100% | Proposed patches must compile on the first run in sandbox. |
| **System** | Ingestion-to-Triage SLA | < 30 seconds | Log receipt to root cause isolation. |

---

## 2. Qualitative User Metrics
*   **Developer Trust score:** Measured via periodic feedback forms evaluating if developer trust in automated patch generation is increasing.
*   **Context-Switching Reduction:** Measured by tracking the average number of local git checkouts developers execute to fix build failures.
