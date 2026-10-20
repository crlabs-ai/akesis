# Akesis Developer Control Plane (Web UI)

The official high-density SRE/DevOps operational console for **Akesis V1**.

## Architecture & Technology Stack

- **Framework:** React 19 + TypeScript
- **Bundler / Dev Server:** Vite
- **Routing:** React Router v7 (with deep link support)
- **Styling:** Vanilla CSS design system (near-black `#080c10`, dark charcoal `#11161f` panels, `#242f3e` borders, monospace logs/diffs, accessible contrast, reduced-motion support)
- **Icons:** Lucide React
- **Testing:** Vitest + React Testing Library + `@testing-library/jest-dom`

---

## Local Development & Prerequisites

### 1. Backend Prerequisite
Before launching the frontend, ensure the Akesis backend API server is running on `http://127.0.0.1:8000`:
```bash
# In the repository root
uv run uvicorn src.apps.api.main:app --host 0.0.0.0 --port 8000
```

### 2. Install Dependencies
```bash
cd web
npm install
```

### 3. Launch Development Server
```bash
npm run dev
```
The Vite development server will launch on `http://localhost:5173`.

---

## Vite Proxy & API Routing Behavior

In development mode, Vite is configured (`vite.config.ts`) with reverse-proxy rules:
- `/v1/*` $\rightarrow$ `http://127.0.0.1:8000/v1/*`
- `/health/*` $\rightarrow$ `http://127.0.0.1:8000/health/*`

Because browser API requests use relative paths (e.g. `/v1/incidents`), no local CORS configuration or port hardcoding is required.

---

## Production Deployment & Reverse Proxy Assumptions

In production:
1. Static frontend assets are built with `npm run build` and served via Nginx, Caddy, AWS CloudFront, or Cloudflare Pages.
2. The reverse proxy maps:
   - `/` $\rightarrow$ Static files (`dist/index.html` with SPA fallback)
   - `/v1` $\rightarrow$ Akesis FastAPI backend service
   - `/health` $\rightarrow$ Akesis FastAPI backend service
3. **No shared API secrets in static client code:**
   - Client code must **never** bundle or store shared API secrets (`read_api_token`, `admin_api_token`, GitHub App keys, Slack signing secrets, Gemini API keys).
   - Any secret bundled into frontend JavaScript is publicly extractable by end users.
   - When Akesis runs behind an identity-aware proxy (IAP), SSO, or Cloudflare Access, authentication is enforced at the network/proxy layer.

---

## Commands & Verification

| Command | Description |
| :--- | :--- |
| `npm run dev` | Start local Vite development server on port 5173 |
| `npm run typecheck` | Run strict TypeScript validation (`tsc -b`) |
| `npm test` | Run Vitest unit & integration test suite |
| `npm run build` | Compile optimized production bundle to `dist/` |
| `npm run preview` | Preview production build locally |

---

## Views & Operational Workspaces

1. **Overview / Command Center (`/`):** Real-time API liveness & readiness status, recent incidents, recent pipelines, and operational counts accurately derived from loaded records.
2. **Incidents List (`/incidents`):** Dense table of CI incidents with search and category filters. Excludes patch diff content to minimize network transfer.
3. **Incident Detail Workspace (`/incidents/:incidentId`):** On-demand incident inspection featuring root-cause diagnosis, syntax-highlighted unified diff viewer, sandbox stdout/stderr execution terminal logs, and GitHub Pull Request link.
4. **Pipeline Explorer (`/pipelines`):** Full audit view of orchestrator pipeline executions, linking diagnosis, fix proposal, and approval records without exposing internal raw JSON blobs.
5. **System Health (`/health`):** Live API probe inspector (`/health/liveness`, `/health/readiness`) with full transparency on backend isolation boundaries.
