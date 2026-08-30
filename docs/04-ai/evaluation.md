# AI Evaluation Methodology: Akesis

---

## 1. Evaluation Hierarchy & Separation

To ensure scientific rigor, AI performance in Akesis is evaluated across four decoupled layers:

```text
[Layer 1: Log Triage Accuracy] ──> [Layer 2: Root Cause Accuracy] ──> [Layer 3: Sandbox Pass Rate] ──> [Layer 4: User Acceptance Rate]
```

1.  **Layer 1 (Log Triage):** Evaluates whether the parser correctly identifies error category (`LINT`, `DEPENDENCY`, `FLAKY`).
2.  **Layer 2 (Root Cause):** Evaluates whether the identified file, line number, and error explanation match ground truth.
3.  **Layer 3 (Sandbox Pass Rate):** Measures whether the synthesized patch compiles and passes test suites in Docker.
4.  **Layer 4 (User Acceptance):** Measures the percentage of delivered PRs accepted and merged by human developers.

---

## 2. Golden Benchmark Dataset
*   Akesis maintains a version-controlled benchmark dataset (`tests/benchmarks/golden_dataset/`) containing **200 real-world CI failure traces** with verified human patches across Python, Node.js, and Go repositories.
*   CI runs benchmark regressions on every prompt or model strategy update.
