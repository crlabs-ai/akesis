# Akesis Documentation System

Welcome to the authoritative engineering knowledge base for **Akesis**.

```text
Ecosystem: CRLabs AI
Governance: Architecture & Engineering Board
System: Closed-Loop CI/CD Failure Remediation Platform
Baseline: Approved V1 Vertical Slice (ADR-0001 through ADR-0007)
```

---

## 1. Documentation Index & Navigation Map

```text
docs/
├── architecture/         # High-Level & Low-Level Design Specifications
│   ├── HLD.md            # System Context, Logical Components, Workflows, Security Perimeters
│   └── LLD.md            # Module Map, Runtime Sequence, ER Schema, State Machines, Test Mapping
│
├── api/                  # REST API Reference
│   └── README.md         # Endpoints, Headers, HMAC Auth, Request/Response Schemas
│
├── deployment/           # Deployment & Infrastructure Guide
│   └── README.md         # Prerequisites, Secrets, Systemd, Nginx Config, Health Checks
│
├── operations/           # Incident Operations & Troubleshooting Runbook
│   └── runbook.md        # Correlation Tracking, Troubleshooting Runbooks, Routine Maintenance
│
├── 00-foundation/        # Vision, Mission, Charter, Principles, Terminology
│   ├── project-charter.md    # Executive summary, boundaries, governance
│   ├── vision.md             # Long-term platform aspirations
│   ├── mission.md            # Concrete engineering mission
│   ├── product-principles.md # Guiding design & safety principles
│   └── terminology.md        # Standardized project glossary
│
├── 01-product/           # PRD, Personas, Use Cases, User Flows, Requirements, Scope
│   ├── prd.md                # V1 Product Requirements Document
│   ├── personas.md           # Developer & DevOps user archetypes
│   ├── use-cases.md          # Lint, Dependency, Flaky-test use cases
│   ├── user-flows.md         # Step-by-step developer interaction flows
│   ├── requirements.md       # Functional & non-functional requirements
│   ├── scope.md              # In-scope vs Out-of-scope boundaries
│   └── success-metrics.md    # MTTR, pass rate, acceptance metrics
│
├── 02-architecture/      # Domain Architecture Specifications
│   ├── system-architecture.md      # Modular monolith control plane architecture
│   ├── component-architecture.md   # Subsystem responsibilities matrix
│   ├── agent-architecture.md       # Bounded state machine agent design
│   ├── data-flow.md                # Data movement & customer privacy guarantees
│   ├── integration-architecture.md # GitHub, Gemini, Slack, Docker interfaces
│   └── deployment-architecture.md  # Single-host deployment model
│
├── 03-engineering/       # Engineering, Coding, Testing, Security Standards
│   ├── engineering-standards.md # Core technical principles
│   ├── coding-standards.md      # Python 3.12, typing, linting guidelines
│   ├── testing-strategy.md      # Unit vs Integration testing pyramid
│   ├── git-workflow.md          # Trunk-based workflow & conventional commits
│   ├── security.md              # Secrets, Docker isolation, prompt injection defense
│   └── definition-of-done.md    # Quality gates for merging features
│
├── 04-ai/                # AI Agent Behavior, Rules, Prompts, Evaluation
│   ├── agent-behavior.md        # Deterministic state machine constraints
│   ├── ai-engineering-rules.md  # Safe LLM integration patterns
│   ├── prompt-engineering.md    # Diagnostic & fix prompt designs
│   ├── model-strategy.md        # Google Gemini structured generation
│   ├── evaluation.md            # Benchmark evaluation methodology
│   └── safety-rules.md          # Non-negotiable safety guardrails
│
├── 05-development/       # Development Workflow, Phase Plan, Local Setup Guide
│   ├── development-workflow.md  # PR lifecycles and review guidelines
│   ├── phase-plan.md            # Authoritative 9-phase engineering sequence
│   └── local-development.md     # Local workstation setup instructions
│
├── 06-decisions/         # Architecture Decision Records (ADRs)
│   ├── README.md                                              # ADR Index & Format
│   ├── ADR-0001-project-foundation.md                         # Specification-Driven Foundation
│   ├── ADR-0002-gemini-diagnostic-baseline.md                 # Gemini AI Diagnostic Baseline
│   ├── ADR-0003-codebase-context-retrieval.md                 # Codebase Context Retrieval
│   ├── ADR-0004-fix-proposal-engine.md                        # Fix Proposal Engine
│   ├── ADR-0005-docker-sandbox-validation.md                  # Docker Sandbox Validation
│   ├── ADR-0006-human-in-the-loop-approval.md                 # Human-in-the-Loop Approval Gate
│   └── ADR-0007-github-pr-creation.md                         # Safe GitHub PR Creation
│
└── future-scaling.md     # Future Production Scaling Roadmap (Distributed Architecture)
```

---

## 2. Document Authority Hierarchy

When resolving requirements or architectural decisions, strictly adhere to the precedence defined in [`AGENTS.md`](../AGENTS.md):

1. **LEVEL 1:** Explicit decisions made by the project owner (prompts & direct instructions).
2. **LEVEL 2:** Approved product documentation (`docs/00-foundation/`, `docs/01-product/`).
3. **LEVEL 3:** Approved architecture documentation (`docs/02-architecture/`, `docs/architecture/`).
4. **LEVEL 4:** Approved ADRs (`docs/06-decisions/`).
5. **LEVEL 5:** Engineering standards (`docs/03-engineering/`, `docs/04-ai/`).
6. **LEVEL 6:** Existing validated implementation code.
7. **LEVEL 7:** General software engineering best practices.
8. **LEVEL 8:** AI inference.

---
<sub>**CRLabs AI** · Systems Thinking · Technical Rigor</sub>
