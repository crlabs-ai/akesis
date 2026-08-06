# User Personas

This document maps target user groups, their current engineering pain points, and how Akesis addresses them.

---

## 1. Startup Founder
*   **Core Goals:** Deliver customer value quickly; minimize infrastructure burn rates.
*   **Pain Points:** Spends valuable time fixing broken node packages or pipeline configs instead of building product.
*   **Daily Workflow:** Mixed coding, product specs design, and system administration.
*   **Current Tooling:** GitHub Actions, Vercel, Slack.
*   **Akesis Value Proposition:** Automatically resolves trivial lint and dependency build errors, letting founders focus entirely on product design.

---

## 2. Backend Engineer
*   **Core Goals:** Ship clean, maintainable logic; minimize PR review iteration cycles.
*   **Pain Points:** Blocked by flaky integration tests or minor formatting failures introduced by teammates.
*   **Daily Workflow:** Local feature design, peer code reviews, and database migrations.
*   **Current Tooling:** VS Code, Git, PostgreSQL, Docker.
*   **Akesis Value Proposition:** Receives a proposed fix for build failures instantly, reducing context-switching latency.

---

## 3. DevOps Engineer
*   **Core Goals:** Maximize pipeline availability; enforce security defaults.
*   **Pain Points:** Constantly triage compiler or environment-specific failures across different teams.
*   **Daily Workflow:** Writing CI scripts, managing secrets, and configuring runtime environments.
*   **Current Tooling:** GitHub Actions, Terraform, AWS IAM.
*   **Akesis Value Proposition:** Offloads standard build remediation from DevOps teams, letting them focus on high-impact infrastructure design.

---

## 4. Platform Engineer
*   **Core Goals:** Build internal developer platforms (IDPs); standardize developer toolchains.
*   **Pain Points:** Standardizing lint and code-quality rules without generating developer pushback.
*   **Daily Workflow:** Maintaining developer environments and monitoring pipeline telemetry.
*   **Current Tooling:** Kubernetes, ArgoCD, Prometheus.
*   **Akesis Value Proposition:** Integrates as a background validation step, silently repairing basic standard violations.

---

## 5. Engineering Manager
*   **Core Goals:** Maintain sprint velocity; eliminate development blockages.
*   **Pain Points:** Review cycles stalling due to broken pipelines on features branches.
*   **Daily Workflow:** Sprint planning, review gating, and cross-team coordination.
*   **Current Tooling:** Jira, Slack, GitHub Projects.
*   **Akesis Value Proposition:** Minimizes pipeline downtime, keeping features moving smoothly through the sprint board.

---

## 6. Open Source Maintainer
*   **Core Goals:** Maintain project quality; review external contributions.
*   **Pain Points:** Triage of broken PRs submitted by first-time contributors.
*   **Daily Workflow:** Reviewing public issues, merging commits, and writing release notes.
*   **Current Tooling:** GitHub UI, git cli.
*   **Akesis Value Proposition:** Auto-analyzes contributor failures and comments on the PR with specific validation details, offloading review work.
