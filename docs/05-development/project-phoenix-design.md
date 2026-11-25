# Project Phoenix: Frontend Design Report

Status: working document, not yet committed. Describes the frontend transformation of the Akesis
operator console. It covers the reference analysis, the design decisions, what shipped, and what
does not exist yet. The API integration rules live in
[frontend-api-integration-matrix.md](frontend-api-integration-matrix.md) and are not restated here.

## 1. Reference analysis

Three references were provided. Each contributes principles. None is reproduced.

### Workflow editor reference (dark, violet accent, canvas with inspector and logs)

- **Visual language:** layered near-black surfaces, a thin border on each layer, one saturated
  accent used only for the primary action and the active state.
- **Layout:** left navigation, central canvas, a right inspector with a fixed-width label column,
  and two bottom panels (conversation and live logs).
- **Why it works:** the accent is scarce, so when it appears it means "this is the thing to act on".
  The inspector keeps detail beside the work rather than in a new page. Logs get their own panel so
  they never compete with the canvas.
- **Adopted:** scarce accent, inspector-style detail (implemented as the delivery detail drawer),
  separate log surfaces.
- **Rejected:** a chat box as the centre of the product (Akesis is a control plane, not a chatbot),
  glowing agent nodes (decoration that does not carry information), and integration icons for
  services Akesis does not connect to.

### Operations dashboard reference (KPI tiles, grouped sidebar, status summary)

- **Visual language:** a large value, a small uppercase label, and a coloured delta beneath.
  Sidebar sections are labelled and separated.
- **Why it works:** the number is the first thing the eye reads, and the label is only a key. Section
  labels make a long menu scannable without reading every item.
- **Adopted:** tile hierarchy (value dominant, label small, semantic colour only on the value's
  state) and labelled, grouped navigation (implemented as "Operate" and "Delivery & health").
- **Rejected:** sparklines and pie charts with no backend history behind them, benchmark bands such
  as "Elite / High / Low" with no source, and the decorative gradient along the sidebar edge.

### Tool dashboard reference (filter chips, status badges, stage stepper)

- **Visual language:** a row of filter chips above a dense grid, status badges, and a horizontal
  stepper where each stage shows its own status.
- **Why it works:** chips make the active filter visible at a glance. A stepper shows sequence and
  state without a chart.
- **Adopted:** chip filters (delivery status), and a stage stepper for pipelines, labelled as
  topology because the backend reports the current status, not per-stage progress.
- **Rejected:** per-card metrics such as request counts, spend and success rates. Akesis has no
  backend source for any of them. Gradient area charts.

### Principles extracted

1. **One accent, used sparingly.** Indigo marks focus and the active item. It never decorates.
2. **Numbers carry the weight.** Labels stay small and muted.
3. **One status vocabulary.** Red means failed or exhausted, amber means held or warning, blue means
   retryable or informational, green means healthy or verified. The same meaning everywhere.
4. **Navigation names its destinations.** Labelled groups; no icon-only rail.
5. **Detail opens beside the work.** Drawers for single records, never a new page for a row.
6. **No chart without history.** A table or a plain count is more honest than a chart that invents
   a series.
7. **Missing data is shown as missing.** "Not reported", "n/a", and "page scope" are visible states,
   never blank or zero.

## 2. Existing frontend audit

| Area | Before this work (committed `dbdc840`) | Now |
| :--- | :--- | :--- |
| Framework | React 19.2, TypeScript ~6.0, Vite 8, react-router-dom 7 | Unchanged |
| Routes | `/`, `/incidents`, `/incidents/:id`, `/pipelines`, `/deliveries`, `/health` | Unchanged; no route added |
| Navigation | Icon-first rail, labels only when expanded; one flat list | Two labelled groups, shared route config, palette |
| Command Center | Summary tiles with a dash while loading; "Loaded" counts | Status panel with per-component sources; skeleton placeholders; counts labelled as page scope; no chart |
| Webhook deliveries | Raw table, pagination, filter | Summary, search over the loaded page, attempt meters, human failure text, copy, drawer |
| Health | Stale "Probes Active" badge after a failed probe; readiness described as if it checked the database | Failed probe clears old values; readiness stated as a constant; worker row says "not reported" |
| Pipelines | Stage graph labelled as topology; real failure reasons | Unchanged (already honest) |
| Incidents | Table skeleton; page-scoped filter | Unchanged |

Baseline and after screenshots are in the working scratchpad (see section 6). They are not part of
the repository.

## 3. Design proposal (as implemented)

- **Identity:** graphite and charcoal surfaces (`#08090b` app, `#0d1013` sidebar, `#12161a` raised).
  Indigo `#8b8cf6` is the accent. Semantic colours are reserved for status.
- **Typography:** Inter for text, JetBrains Mono only for identifiers, hashes, commands and raw
  values. Scale: 12, 13, 14, 16, 20, 28 px. The smallest text is 12 px.
- **Spacing and radius:** radii 4, 6, 10 px and a pill for progress bars. Gaps are 8, 12, 16 px.
- **Elevation:** surfaces separate by value and border, not shadow. Shadow is used only on floating
  panels (drawer, palette).
- **Glass:** used only on the command palette, which is a floating surface. Tables and status panels
  stay solid so dense data stays readable.
- **Motion:** 120 ms for hover and colour, 200 ms for panels, the standard ease-out curve. Skeletons
  sweep slowly. Every animation is disabled under `prefers-reduced-motion`.
- **Focus:** a visible indigo outline on keyboard focus.

## 4. Architecture decision

**Refactor, not rebuild.** The routes, API client, auth store, error handling and tests are sound and
verified against the real backend. A rebuild would have discarded that work and risked the integration
guarantees (no empty state on failure, token never in the bundle) for no gain in capability. Changes
are confined to the shell, the design tokens, and the three pages whose content was misleading or
unfinished. The palette and the route groups are new modules; nothing else depends on them.

## 5. What shipped in this phase

- Navigation grouped into labelled sections, with a shared route definition (`web/src/lib/routes.ts`).
- Command palette (Ctrl+K / ⌘K, header button): navigates the real routes only. Label matches rank
  above group matches.
- Command Center: placeholders sized to their values while loading, so tiles do not shift.
- Health: a failed probe no longer presents old results as current; readiness is described as a
  constant that does not check the database; the retry worker is stated as not reported.
- Deliveries workspace (earlier in this phase).
- Accessibility fixes from the final review:
  - Muted text raised from 4.0:1 to 5.7:1 on the app surface (WCAG AA for small text).
  - Dialog drawer keeps Tab and Shift+Tab inside the dialog and closes on Escape.
  - Collapsed navigation groups keep an accessible name.
  - Loading placeholders no longer announce "Loading count" repeatedly.
  - Delivery IDs are the keyboard target for opening details; rows are no longer focusable.
  - Health boundary list rewritten to state only what the page can verify, replacing claims about
    backend internals the browser cannot see.
- Tests: 70 frontend tests pass across 13 files, including the palette, navigation groups, health
  honesty and drawer focus trap.

## 6. Verification

- Typecheck, production build, and 70 unit tests pass. Lint: 14 warnings, of which 2 are
  pre-existing in the rail and the rest follow the load-on-mount pattern used across the app.
- Browser checks against the real backend, with a disposable database seeded through the signed
  webhook path: all five routes render live data after the token is set; zero console errors in the
  final session.
- Phone width (390 px): no horizontal overflow on any route, measured at the real layout.
- Before and after screenshots of the four pages that changed are kept in the working scratchpad,
  outside the repository.

## 7. Remaining gaps (not built, and why)

- **Agent Activity, Sandbox, Approvals:** no backend routes exist. Building pages for them would show
  activity that the system does not record. Each needs a backend capability first.
- **Trend charts:** need a delivery history endpoint (gap G1 in the integration matrix).
- **Worker and database health:** need a backend status endpoint (G2, G6).
- **Remediation hold as live status:** stated as policy (ADR-0010), not read from the backend (G3).
- **Global search over incidents:** the palette searches pages only. Searching incident records
  would need a search endpoint.
- **Per-page skeletons for Pipelines timelines and Health detail drawers:** the existing table
  skeletons cover the list views. Timeline and drawer skeletons are not built.
- **Screen-reader audit:** not performed. Keyboard, focus and naming behaviour is covered by unit
  tests and a browser check, not by assistive technology.
- **Backend regression suite:** not run in this phase. No backend code changed.
- **Reduced motion:** CSS animations and transitions are disabled under the preference, and the GSAP
  tweens check it too. Not verified with an actual reduced-motion browser setting.

## 8. Quality gate

- **First-time user:** the labelled groups and palette name every destination. Pages state their
  scope and their limits.
- **DevOps engineer:** deliveries show status, attempts, the human failure meaning and the next retry.
  Health states what it cannot check.
- **Product designer:** one accent, one status vocabulary, one type scale. Still not as polished as
  a dedicated design team would produce; the pipeline stage row in particular is dense.
- **Senior frontend engineer:** route definitions are shared; state is local per page; the auth
  boundary is unchanged.
- **Security engineer:** no fabricated data, no requeue, no approval or execution controls, no
  secrets in the bundle. Unavailable capabilities are named as unavailable.
