# AI Evaluation Methodology: Akesis V1

---

## 1. V1 Evaluation Scope: 10–12 Representative Scenarios
Akesis V1 evaluates remediation accuracy against **10–12 carefully designed representative test scenarios** covering the approved V1 capabilities:
1. **Lint Remediation:** Formatting and style errors in Python (Ruff/Black) and TypeScript (ESLint/Prettier).
2. **Dependency Resolution:** Missing package imports and lockfile discrepancies in `uv` and `npm`.
3. **Flaky-Test Quarantine:** Non-deterministic test assertions evaluated across repeated runs.

---

## 2. Evaluation Layers
* **Layer 1 (Diagnosis Correctness):** Measures accuracy of error classification, file target, and line identification.
* **Layer 2 (Patch Correctness):** Measures whether the synthesized diff compiles and resolves the error.
* **Layer 3 (Sandbox Validation Success):** Verifies that the containerized build exits with code 0.
* **Layer 4 (Human Acceptance):** Tracks acceptance rate of delivered PRs.

*(Note: Large-scale 200+ case golden benchmark suites are documented under [`docs/future-scaling.md`](../future-scaling.md)).*
