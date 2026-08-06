# Contributing to Akesis

This document defines the process for contributing code, designs, or specifications to the Akesis repository.

---

## Local Workstation Setup

*Detailed setup instructions will be drafted when the code environment is initialized.*

### Core Tool Prerequisites
*   Docker & Docker Compose (for sandboxed local verification)
*   Python 3.12+ / Go 1.22+ (depending on target component)
*   Terraform (for infrastructure work)

---

## Development Workflow

We use trunk-based development with short-lived feature branches:

1.  **Branch Branching:** Branch from `main` using naming convention `[type]/[ticket-id-or-short-desc]` (e.g., `feat/akesis-212-db-schema`).
2.  **Implementation & Test:** Write tested, documented code complying with standards.
3.  **Self Review:** Review your own diff before requesting peer attention.
4.  **Pull Request:** Submit using the PR template. Ensure all automated CI lint checks pass.
5.  **Merge:** Squash-and-merge after peer approval.

---

## Commit Style Standard

Commit messages must comply with the Conventional Commits 1.0.0 specification:

```text
<type>(<scope>): <description>
```

Approved types: `feat`, `fix`, `docs`, `refactor`, `chore`, `test`, `style`, `ci`, `perf`.

---
*Return to [README](README.md)*
