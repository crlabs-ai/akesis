# CLAUDE.md

Read `AGENTS.md` first. It is the authoritative instruction manual for this repository. This file adds Claude-specific working rules and does not replace it.

## Project

- Akesis is an AI-assisted self-healing CI/CD remediation platform (V1 vertical slice).
- Backend: Python 3.12+, managed with `uv`. Canonical commands: `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run mypy .`.
- Frontend: React + TypeScript + Vite under `web/`. Scripts: `npm run dev`, `npm run typecheck`, `npm run build`, `npm test`, `npm run lint`.

## Frontend rules

- Frontend-only tasks must not change backend code, API contracts, database models, migrations, authentication, or deployment configuration.
- Preserve real API integrations. Never add fabricated runtime data or fake telemetry.
- Incident lists must not fetch full diffs. Fetch diffs on the incident-detail route only.
- Design direction: restrained GitHub Dark / VS Code-inspired engineering workstation. Near-black surfaces, readable typography, monospace for technical data, minimal borders, subdued status colours, functional terminal and diff viewer.
- Avoid generic AI-dashboard styling: no gradients, neon, excessive cards, or decorative charts.

## Working rules

- Before editing, read the relevant files and the existing `git diff`. Preserve unrelated user changes.
- Make the smallest coherent change that solves the task. No gratuitous refactoring or formatting.
- Do not commit, push, merge, or switch branches unless explicitly instructed.
- After a frontend change, run the relevant tests, `npm run typecheck`, and `npm run build`. Run the full test suite when the change is non-trivial, not for small CSS adjustments.
- If requirements conflict or fall outside V1 scope, stop and ask, as `AGENTS.md` §5 requires.
