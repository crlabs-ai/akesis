# Frontend–Backend API Integration Matrix

Status: current as of Phase 16. Every row was checked against the source, not against the
frontend's own claims. Use this document to decide what the dashboard may present as live data.

## 1. Architecture facts

| Area | Current state |
| :--- | :--- |
| Framework | React 19.2, TypeScript ~6.0, Vite 8, react-router-dom 7, gsap, lucide-react |
| Test stack | Vitest 5, jsdom, Testing Library. Unit tests stub `apiClient` methods with `vi.spyOn` |
| Entry | `web/src/main.tsx` renders `App.tsx` inside `BrowserRouter` |
| Routes | `/` Overview, `/incidents`, `/incidents/:incidentId`, `/pipelines`, `/health`; anything else redirects to `/` |
| API client | `web/src/services/api.ts`: one `request` function and an `apiClient` object. Relative URLs |
| Authentication (frontend) | None before Phase 16. The backend read endpoints required a token the frontend never sent |
| Authentication (backend) | Phase 2 (ADR-0012): GitHub sign-in sets an HttpOnly session cookie; every tenant route requires a session and workspace membership. The global read token is removed |
| State | Local `useState` per page. `App` remounts the view on manual refresh |
| Styling | `web/src/index.css` with CSS custom-property tokens (GitHub Dark palette). No CSS framework |
| Environment | No `.env` in `web/`. Vite proxies `/v1` and `/health/` to the backend |
| Proxy target | Before Phase 16: `127.0.0.1:8001`. The backend documentation says 8000. Corrected in Phase 16 |

## 2. Backend API surface (read endpoints)

| Method | Path | Auth | Request | Response | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GET | `/health/liveness` | none | — | `{status}` | Process responding |
| GET | `/health/readiness` | none | — | `{status, environment}` | **Returns a constant.** It does not check the database |
| GET | `/v1/workspaces/{workspace_id}/incidents` | session + membership | `limit` 1–100, `offset` ≥ 0 | `IncidentSummary[]` | Derived from pipeline rows. No total count |
| GET | `/v1/workspaces/{workspace_id}/incidents/{incident_id}` | session + membership | path | `IncidentDetail` | 404 with `{detail}`, also for another workspace's incident |
| GET | `/v1/workspaces/{workspace_id}/pipelines` | session + membership | `limit` 1–100, `offset` ≥ 0 | `PipelineSummary[]` | No total count |
| GET | `/v1/workspaces/{workspace_id}/deliveries` | session + membership | `status` (repeatable: `retryable_error`, `exhausted`, `provider_hold`), `limit` 1–200, `cursor` (opaque) | `{items, next_cursor}` | Keyset on `(updated_at, delivery_id)`. Ten-field allow-list |

Phase 2 replaced the `/v1/incidents`, `/v1/pipelines` and `/v1/webhook-deliveries` paths and the read token used in the historical table below; the full route list is in `docs/02-architecture/system-architecture.md` §2.6.

Error bodies are FastAPI's `{"detail": ...}`. Validation errors (422) return a list in `detail`.

Frontend TypeScript types match the backend models field for field for `IncidentSummary`,
`IncidentDetail` and `PipelineSummary`. Checked one by one in Phase 16.

## 3. Integration matrix

Status key: **Full** (live, correct, states handled) · **Partial** (live but incomplete or
misleading) · **Mock** (static data) · **Not implemented** (endpoint exists, no UI) ·
**Endpoint missing** (no backend capability to integrate).

| # | Frontend page / component | Backend endpoint | Method | Auth | Request | Response | Status before Phase 16 | Gap / missing functionality | Status after Phase 16 |
| :-- | :--- | :--- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| 1 | Overview: API readiness badge | `/health/readiness` | GET | none | — | `{status, environment}` | Partial | Constant response. The badge says READY even if the database is down | Partial, labelled as a process probe that does not check the database |
| 2 | Overview: "Loaded Incidents" | `/v1/incidents` | GET | read token | `limit=10` | `IncidentSummary[]` | Partial | Count of a 10-item page presented as a total. Requests sent without a token | Relabelled "Newest incidents (page)". Token sent |
| 3 | Overview: "Pending Approvals" | `/v1/incidents` (derived) | GET | read token | `limit=10` | `IncidentSummary[]` | Partial | Counts only the loaded page, presented as a total | Removed. There is no total, and a page-derived count would mislead |
| 4 | Overview: "Loaded Pipelines" | `/v1/pipelines` | GET | read token | `limit=10` | `PipelineSummary[]` | Partial | Page count presented as a total. No token sent | Relabelled as a page count. Token sent |
| 5 | Overview: recent incidents table | `/v1/incidents` | GET | read token | `limit=10` | `IncidentSummary[]` | Partial | No token sent. Errors shown as a notice only | Token sent. Shared error panel |
| 6 | Incidents list | `/v1/incidents` | GET | read token | `limit`, `offset` | `IncidentSummary[]` | Partial | No token. Category and search filter the loaded page only ("X of Y loaded") | Token sent. Page-local filter wording kept. Pagination proven in E2E |
| 7 | Incident detail | `/v1/incidents/{id}` | GET | read token | path | `IncidentDetail` | Partial | No token. Diff, proposal and validation views render backend fields | Token sent. 404 handled |
| 8 | Pipeline explorer | `/v1/pipelines` | GET | read token | `limit`, `offset` | `PipelineSummary[]` | Partial | No token. Stage graph is a neutral topology. Hold not stated on the page | Token sent. ADR-0010 banner added. Failure reason shown as stored |
| 9 | System health view | `/health/liveness`, `/health/readiness` | GET | none | — | `{status}`, `{status, environment}` | Partial | Says "Verified backend probes", but readiness is a constant | Caveat added. Liveness is the only probe that means something |
| 10 | Webhook deliveries list | `/v1/webhook-deliveries` | GET | read token | `status`, `limit`, `cursor` | `{items, next_cursor}` | **Not implemented** | No page, no client method | **Full**: page, filters, cursor pagination, states |
| 11 | Webhook delivery totals (total, verified, retryable, exhausted) | — | — | — | — | — | **Endpoint missing** | No aggregate endpoint. Counting a partial list would mislead | Not shown. See gap G1 |
| 12 | Worker health | — | — | — | — | — | **Endpoint missing** | The worker writes a heartbeat file. The only probe is a CLI command | Not shown. See gap G2 |
| 13 | Remediation hold status | — | — | — | — | — | **Endpoint missing** | No endpoint reports the hold | Stated as static policy (ADR-0010), labelled as not live. See gap G3 |
| 14 | Requeue a delivery | — | — | — | — | — | Not implemented (by decision) | No endpoint exists, by decision | Not built |
| 15 | Approve, reject, merge, open PR | `/v1/slack/interactions` (Slack only) | POST | Slack signature | Slack payload | — | Not applicable | The dashboard has no action buttons | Unchanged. None exist |
| 16 | Operator sign-in and identity | — | — | — | — | — | **Endpoint missing** | No login, no sessions, no operator identity | Runtime read-token entry, session-scoped. See gap G4 |
| 17 | Total counts for incidents and pipelines | — | — | — | — | — | **Endpoint missing** | List endpoints return no total | Pages show "page N" and never a total. See gap G5 |

## 4. Gaps (reported, not implemented)

These require backend decisions. Phase 16 does not invent them.

* **G1. Delivery aggregate endpoint.** A `GET /v1/webhook-deliveries/summary` returning `COUNT(*)` per status would support totals. It needs a backend design decision and a review of what it exposes.
* **G2. Worker status endpoint.** The API cannot report whether the retry worker is alive. An endpoint would have to read the heartbeat file on the same host, or the worker would need to write a database row. Both are design decisions.
* **G3. Remediation hold endpoint.** A read-only endpoint could report `require_verified_provenance()` state. Today the hold is an unconditional code path, and the UI states it as policy.
* **G4. Operator authentication.** One shared read token guards every read endpoint and identifies no operator. Per-operator tokens or sessions are a security design decision.
* **G5. Totals on list endpoints.** Keyset or offset pagination without a count cannot show totals. A count endpoint would need the same review as G1.
* **G6. Readiness does not check the database.** `/health/readiness` returns a constant. Making it check the database is a small backend change, but it changes a health contract, so it needs approval.
* **G7. Frontend auth proxy.** The read token must not live in the browser bundle. The production answer is a reverse proxy that injects the header server-side, so the browser never holds the token. This is documented as the recommended production configuration.

## 5. Reproducing the browser end-to-end run

The run used a disposable PostgreSQL database on loopback and no real GitHub, Slack or Gemini
credentials. GitHub is replaced at the HTTP transport (`tests/support/github_stub.py`). Every
delivery enters through the signed webhook endpoint (`tests/support/e2e_seed.py`). Nothing is
written to the database directly.

1. Start PostgreSQL 16 on `127.0.0.1:55432` with database `akesis_test`, then run
   `alembic upgrade head` against it.
2. Write a `.env`-free working directory. Export `ENVIRONMENT=development`, `DATABASE_URL`,
   `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (a throwaway
   key), and the retry settings. Start `python -m tests.support.e2e_api_process` on port 8000.
3. Start `python -m tests.support.worker_process` with the same environment, so the real worker
   works through the retryable deliveries.
4. Run `python -m tests.support.e2e_seed http://127.0.0.1:8000 <secret file>`.
5. Run `npm run dev` in `web/`. The dev server proxies `/v1` and `/health/` to port 8000.
6. Drive the UI in a browser. Read console messages and network requests after each step.

What the run did and did not cover:

* **Covered:** unauthenticated access, token entry, rejected tokens, token change without a page
  reload, filters, three-page pagination with no repeated rows, backend stopped and restarted,
  the stale banner and its retry, live database states (verified, retryable, provider hold,
  exhausted, rejected), the hold banners, and the console.
* **Not covered:** a 500 from the backend (covered only by unit tests), a proxy timeout, and
  any real GitHub behaviour. Tokens were typed in the browser, and no credential was written to
  any kept file.
