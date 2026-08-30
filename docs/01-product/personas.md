# User Personas: Akesis

---

### Persona 1: Staff Backend Engineer (Alex)
*   **Role:** Senior / Staff Backend Engineer at a high-growth SaaS company.
*   **Goals:** Ship clean, highly reliable distributed services; maintain flow state; reduce PR turnaround time.
*   **Pain Points:** Constantly interrupted by CI failures caused by strict linter rule updates or minor formatting discrepancies across monorepo packages.
*   **Current Workflow:** Pushes branch $ightarrow$ switches task $ightarrow$ receives Slack alert that CI failed on lint $ightarrow$ stashes work $ightarrow$ pulls branch $ightarrow$ runs linter locally $ightarrow$ commits and pushes again.
*   **Akesis Interaction:** Akesis intercepts the failed lint step, runs the formatter in sandbox, and opens a PR into Alex's branch. Alex reviews the 2-line diff on mobile/web and taps "Merge".
*   **Trust Criteria:** Diffs must be surgical. No random reformats of unrelated functions.

---

### Persona 2: Platform / DevOps Engineer (Priya)
*   **Role:** Lead Platform Engineer responsible for CI/CD infrastructure and developer productivity.
*   **Goals:** Maximize pipeline throughput; reduce compute waste on flaky runs; enforce repository standards.
*   **Pain Points:** Spends hours every week fielding developer tickets complaining about broken lockfiles and flaky test runs blocking master merges.
*   **Current Workflow:** Manually inspects CI worker logs, identifies that an npm dependency updated upstream with breaking lockfile checksums, and tells developers how to fix it.
*   **Akesis Interaction:** Akesis identifies lockfile collisions automatically, regenerates the lockfile in a sandbox, verifies compilation, and submits the update.
*   **Trust Criteria:** Strict security isolation. Sandbox containers must never have access to internal network secrets.

---

### Persona 3: Engineering Manager (Marcus)
*   **Role:** Director of Engineering managing 4 squad leads and 25 engineers.
*   **Goals:** High team velocity; low DORA lead time for changes; predictable delivery dates.
*   **Pain Points:** Pull requests stalling in "Red CI" state for 1–2 days because engineers move to other tasks before noticing the failure.
*   **Current Workflow:** Reviews sprint burndown and discovers multiple PRs blocked on trivial build checks.
*   **Akesis Interaction:** Views Akesis weekly metrics dashboard showing 45 hours of developer time saved and 88% patch acceptance.
*   **Trust Criteria:** Comprehensive audit logs and measurable MTTR reduction.

---

### Persona 4: Open Source Maintainer (Elena)
*   **Role:** Core maintainer of a widely-used open-source developer framework.
*   **Goals:** Keep project quality high; encourage first-time contributors.
*   **Pain Points:** First-time contributors frequently submit PRs that fail CI due to missing formatting hooks or lockfile errors, creating massive review burden.
*   **Akesis Interaction:** Akesis posts a friendly, detailed remediation PR or comment on the contributor's fork explaining the exact issue and providing the validated patch.
*   **Trust Criteria:** Polite, evidence-backed explanations that teach contributors rather than confusing them.
