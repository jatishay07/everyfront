# `web/`

**Owner:** CANVAS (persona 6)

Next.js 14 + Tailwind dashboard, dark-mode-first — the demo video is recorded
in dark mode at 1080p. Built against a mocked data layer implementing the
§3.3/§3.4 contracts exactly, so swapping to the real `services/api` is a
one-file change.

---

## Screens (in the WO1–4 priority order from §4 persona 6)

1. **Command Center** (`/`) — the §3.4 stats banner in big tabular numerals,
   live-polling every 4s with zero layout shift; case list with front badges
   and deadline chips (red at ≤7 days).
2. **Case detail** (`/cases/[id]`) — the unlawful-denial banner when flagged;
   a deadline ladder across all applicable fronts; a fronts panel with
   per-front reason, citation chip, deadline chip, and the **Approve & file**
   button (the human-in-the-loop gate, `POST /cases/{id}/approve_filing`);
   the `events/` timeline with agent avatars and citations rendered as
   chips — built to be freeze-frame-readable; a document gallery with
   Verifier pass/fail state.
3. **Live Activity** (`/activity`) — a global, agent-filterable stream of
   every case's events, polling every 3s. The demo's money shot.
4. **Intake** (`/intake`) — a manual new-case form plus a simulated document
   upload with inline Verifier feedback (the "cat photo" case is a one-click
   demo toggle), and quick buttons that call `POST /demo/inject_bill`
   directly.

Everything below the WO1–4 cut line (patient status page, hospital coverage
map, onboarding tour) was intentionally not built — per §4.6, cut first if
time runs out, and WO1–4 were solid before touching it.

## The data layer — one file to swap mock → real

```
lib/types.ts       — TypeScript types mirroring §3.1 / §3.3 exactly
lib/citations.ts   — every regulation string, in one place, matching
                      packages/rules/rules/{deadlines,eligibility,fronts,
                      audit,denial}.py as merged to main
lib/mock-data.ts   — the 8-case synthetic corpus + computeStats()
lib/store.ts       — in-memory mutable store (approve/inject/manual-intake
                      actually mutate state, so the mock feels real)
lib/api.ts         — THE SWAP POINT. Every screen imports only from here.
```

Set `NEXT_PUBLIC_API_BASE_URL` to a real `services/api` deployment and every
function in `lib/api.ts` switches from the in-memory mock to real `fetch`
calls against §3.3 — nothing else in `web/` changes. Unset (the default),
everything runs against `lib/store.ts`, which is seeded from
`lib/mock-data.ts` and mutated in place by "Approve & file", the intake form,
and the inject-fixture buttons.

### Why the mock corpus is not arbitrary

Every field in `DashboardStats` is computed by `computeStats()` over the same
8-case array the UI reads — never hand-typed twice — so the banner can never
drift from the case data behind it (§7: *"a judge doing arithmetic must not
catch a discrepancy."*). The 8 cases were built to hit the §7 target banner
exactly:

> 8 cases · 6 hospitals · 5 deadlines this week · $84,200 billed ·
> 4 charity-eligible · 2 PPDR-eligible · 1 unlawful denial flagged ·
> $9,100 in billing errors found · 11 filings sent · 0 human hours

Verified by hand against `lib/mock-data.ts`'s `buildCases()` at the time of
writing; if you edit the corpus, the arithmetic (not the target banner) is
the source of truth going forward.

Money fields are `_cents` integers everywhere, per §3.1 (including the
2026-08-25 `annual_income` → `annual_income_cents` amendment). Deadline
math avoids `new Date(iso).getFullYear()` on date-only strings — that round
trip through UTC parsing silently shifts the calendar date in any timezone
behind UTC, which would corrupt the ≤7-day red-chip logic the whole banner
depends on. See `lib/format.ts`'s `daysUntil` / `dateOnlyToUTCms` and
`lib/mock-data.ts`'s `computeStats`.

## Running it

```
npm install
npm run dev      # http://localhost:3000, mock data layer by default
npm run build && npm start   # production build, listens on $PORT
```

## Deployment

`Dockerfile` builds a Next.js `standalone` output and listens on `$PORT`, per
`infra/deploy.sh` (ATLAS persona 1):

```
PROJECT_ID=everyfront-hack-2026 ./infra/deploy.sh web
```

Pass `--build-arg NEXT_PUBLIC_API_BASE_URL=https://ef-api-xxxx.a.run.app` at
build time once `services/api` has a stable URL (Next.js inlines
`NEXT_PUBLIC_*` vars at build time, not runtime).

## HANDOFF items for FORGE / SWARM

1. **No `GET /events` (global activity feed) in §3.3.** `getActivityFeed()`
   in `lib/api.ts` currently falls back to fetching every case then
   flattening `events[]` client-side once a real API is live — correct but
   O(cases), and it'll get slow well before this project needs it to.
   Proposing a dedicated `GET /events?since=` endpoint.
2. **No manual-intake endpoint in §3.3.** Only the Gmail watch and
   `POST /demo/inject_bill` create cases in the contract. The Intake screen's
   form (`lib/store.ts`'s `createCaseFromIntake`) is a reasonable real
   product need — an advocate keying in a case by hand — and only works in
   mock mode today; `lib/api.ts`'s `createCase()` returns an honest error
   against a real backend rather than pretending to work. Proposing
   `POST /cases` for this.
3. **`events[].agent` enum.** §3.1 lists `reader|lookup|clock|auditor|
   strategist|filer`; §4 persona 5 WO1 also names a **Verifier** agent (the
   income-doc / cat-photo checks). `lib/types.ts`'s `AgentName` includes
   `"verifier"` so the UI has somewhere to render its events — propose
   adding it to the literal §3.1 enum too.
4. **`Case.denial_flag`.** Not in §3.1, but without it the "1 unlawful denial
   flagged" stat (§3.4) has nowhere to read from. Shaped to match
   `check_denial_lawfulness`'s `DenialCheck` (`packages/rules/rules/
   denial.py`): `{violated, reason, citation}`.

## What's not done (by design, not oversight)

- Lighthouse wasn't run in this sandbox (no headless Chrome available at
  build time here) — the build already leans toward a good score: system
  font stack (no external font fetch blocking FCP), `output: "standalone"`
  for a small server bundle, ~110 kB first-load JS on every route, semantic
  HTML, and no unoptimized images. Worth an actual run against a deployed
  URL before the demo.
- Document upload does not hit a real GCS signed URL yet (no `services/api`
  endpoint for it in §3.3) — `IntakeForm.tsx` simulates the upload/Verifier
  pass client-side and says so in the UI copy, rather than silently faking a
  real network call.

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[CANVAS] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
