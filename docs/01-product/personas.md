# User Personas: Akesis

---

### Persona 1: Backend Engineer (Alex)
* **Role:** Senior Backend Engineer.
* **Goals:** Ship clean, maintainable logic; minimize PR review iteration cycles.
* **Pain Points:** Interrupted by CI failures caused by strict linter updates or minor formatting discrepancies.
* **Akesis Interaction:** Akesis intercepts failed lint steps, runs formatter in sandbox, and opens a clean PR into Alex's branch.
* **Trust Criteria:** Minimal diffs with zero unrelated code modifications.

---

### Persona 2: Platform / DevOps Engineer (Priya)
* **Role:** Lead Platform Engineer responsible for CI/CD infrastructure.
* **Goals:** Maximize pipeline throughput; eliminate repetitive developer triage tickets.
* **Pain Points:** Hours lost triaging broken lockfiles and flaky test runs.
* **Akesis Interaction:** Akesis resolves lockfile collisions in a sandbox and annotates flaky tests automatically.
* **Trust Criteria:** Strict sandbox security isolation and reliable non-root execution.

---

### Persona 3: Engineering Manager (Marcus)
* **Role:** Engineering Manager.
* **Goals:** High team velocity; low DORA lead time for changes.
* **Pain Points:** Pull requests stalling in "Red CI" state because engineers switch tasks.
* **Akesis Interaction:** Observes reduced MTTR and improved PR turnaround.
* **Trust Criteria:** Evidence-based PRs that are easy for developers to verify.

---

### Persona 4: Open Source Maintainer (Elena)
* **Role:** Core maintainer of an open-source project.
* **Goals:** Maintain high code quality; support external contributors.
* **Pain Points:** First-time contributors frequently submit PRs that fail basic lint checks.
* **Akesis Interaction:** Akesis opens an automated fix PR or comment explaining the issue and providing the validated patch.
* **Trust Criteria:** Polite, educational explanations.
