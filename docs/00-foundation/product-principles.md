# Product Principles: Akesis

These non-negotiable principles govern every product and engineering decision:

---

### 1. Human-in-the-Loop Control
Akesis empowers engineers; it does not bypass them. In V1, all automated code modifications are submitted as Pull Requests requiring explicit human review and approval.

### 2. Verification Before Action (Zero Unvalidated Output)
Every proposed code patch must be verified inside an isolated Docker sandbox by executing the failing build or test command. Patches that fail compilation or emit warnings are discarded.

### 3. The Minimal Diff Principle
Remediation must be surgical. Akesis modifies only the exact lines necessary to resolve the failure, avoiding cosmetic refactoring or style drift on unrelated code.

### 4. Evidence-Based Explainability
Every Akesis Pull Request includes the original stack trace, root cause explanation, and reproduction output from the sandbox validation run.

### 5. Fail-Closed Safety
If confidence is low or sandbox validation fails, Akesis fails closed. It logs diagnostic context and refrains from submitting speculative code.

### 6. Pragmatic Simplicity
Akesis V1 is engineered as a robust vertical slice. We deliberately avoid premature distributed infrastructure, complex proxy layers, or speculative multi-agent routing.
