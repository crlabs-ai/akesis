# Success Metrics & Target KPIs: Akesis V1

This document defines the quantitative targets and measurement methodologies for evaluating Akesis V1.

---

## 1. Quantitative Engineering Targets

| Metric Name | Target | Measurement Method | Operational Value |
| :--- | :--- | :--- | :--- |
| **Patch Acceptance Rate (PAR)** | $> 85\%$ | $\frac{\text{Merged Akesis PRs}}{\text{Total Delivered Akesis PRs}} \times 100$ | Measures developer trust and code precision. |
| **Mean Time to Remediate (MTTR)** | $< 5\text{ min}$ | Webhook receipt timestamp to PR creation timestamp. | Measures speed and context-switch elimination. |
| **Sandbox Validation Rate** | $100\%$ | $\frac{\text{PRs with Verified 0 Exit Code}}{\text{Total Delivered PRs}} \times 100$ | Enforces zero broken code delivered to users. |
| **False Positive Diagnosis Rate** | $< 5\%$ | Percentage of diagnoses rejected by developer as incorrect root cause. | Evaluates diagnostic reasoning quality. |

---

## 2. V1 Benchmark Evaluation Suite
* Performance is evaluated against **10–12 representative test scenarios** covering:
    * Python lint errors (Ruff/Black)
    * TypeScript/JavaScript lint errors (ESLint/Prettier)
    * Lockfile checksum and missing package collisions (uv, npm)
    * Non-deterministic test runs (flaky pytest assertions)
* Measures: Diagnosis correctness, patch correctness, sandbox validation success, and human acceptance.
* *(Note: A target is a design goal, not a fabricated result).*
