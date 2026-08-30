# Product Principles: Akesis

These non-negotiable principles govern every product, architectural, and engineering decision in Akesis.

---

### 1. Human-in-the-Loop Control
Akesis empowers engineers; it does not bypass them. In V1, all automated code modifications must be submitted as Pull Requests requiring explicit human review and approval. Zero unattended merges to user branches.

### 2. Verification Before Action (Zero Unvalidated Output)
An unvalidated AI suggestion is technical debt. Akesis must verify every proposed code patch inside an isolated sandbox container by re-running the failing build or test suite. If a patch does not compile cleanly with zero warnings, it is never presented to the developer.

### 3. The Minimal Diff Principle
Remediation must be surgical. Akesis only modifies the exact lines required to resolve the failure. It is strictly prohibited from making gratuitous formatting changes, renaming unrelated identifiers, or applying cosmetic refactorings during a bugfix.

### 4. Evidence-Based Explainability
Developers trust systems that explain *why*. Every Akesis Pull Request must link the original error stack trace, provide a clear explanation of the root cause, and attach reproduction output from the sandbox validation run.

### 5. Fail-Closed Safety
If the agent runtime experiences low confidence, cannot reproduce the failure, or fails sandbox validation after bounded retry attempts, it must fail closed. It flags the incident, provides diagnostic context, and refrains from submitting speculative code.

### 6. Least Privilege by Design
Akesis requests and operates with the absolute minimum permissions required. It accesses only the repositories, logs, and tokens necessary for remediation. Customer code is processed ephemerally and never persisted in unencrypted or untrusted storage.
