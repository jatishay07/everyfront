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

Set `API_BASE_URL` (a plain server env var — see below) to a real
`services/api` deployment and every function in `lib/api.ts` switches from
the in-memory mock to real `fetch` calls against §3.3 — nothing else in
`web/` changes. Unset (the default), everything runs against `lib/store.ts`,
which is seeded from `lib/mock-data.ts` and mutated in place by "Approve &
file", the intake form, and the inject-fixture buttons.

### Runtime, not build-time — and why

`API_BASE_URL` is intentionally **not** `NEXT_PUBLIC_API_BASE_URL`. Next.js
inlines `NEXT_PUBLIC_*` vars into the client bundle at `next build` time,
which is exactly wrong for two reasons discovered wiring this up against the
real API:

1. **Cloud Run repointing.** `gcloud run services update ef-web
   --set-env-vars=API_BASE_URL=...` rolls a new revision of the *same image*
   in seconds. A build-time var would mean rebuilding the container to
   switch backends, or to fall back to mock mid-demo if the API goes down
   (§6 risk register treats that as a live risk).
2. **CORS.** `https://ef-api-756591166292.us-central1.run.app` sends no
   `Access-Control-Allow-Origin` header (verified: an OPTIONS preflight to
   `/cases` gets a bare 405, no CORS headers at all). A client-side
   `fetch()` straight at that origin from the deployed `web` origin would be
   blocked by the browser, full stop — this isn't a nice-to-have.

Both are solved by two server-side Route Handlers that read `API_BASE_URL`
fresh on every request:

```
app/api/config/route.ts         — { usingMock: boolean }, read once by lib/api.ts
app/api/proxy/[...path]/route.ts — forwards GET/POST to API_BASE_URL, server-to-server
```

The browser only ever calls same-origin `/api/config` and `/api/proxy/*`.
`lib/api.ts`'s `usingMock()` caches that one config fetch for the page's
lifetime; every data function does `if (await usingMock()) { …mock… } else {
…realFetch…(which hits /api/proxy)…}`.

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

Set the runtime env var on the Cloud Run service once it's up (see the
"Runtime, not build-time" section above) — no build-arg, no rebuild:

```
gcloud run services update ef-web --region=us-central1 \
  --set-env-vars=API_BASE_URL=https://ef-api-756591166292.us-central1.run.app
```

## HANDOFF items for FORGE / SWARM / ATLAS

Status as of pointing `web/` at the live API
(`https://ef-api-756591166292.us-central1.run.app`), all verified via curl:

1. **`GET /events` is live-broken.** §3.3 added this exact endpoint for the
   global activity feed (previously HANDOFF item 1 here) — but
   `curl {API}/events` and `curl {API}/events?limit=10` both return a bare
   `500 Internal Server Error` with no JSON body. `getActivityFeed()` in
   `lib/api.ts` now tries the real endpoint first and silently falls back to
   the previous per-case-flatten approach on failure, so the feed keeps
   working either way — but the dedicated endpoint should get fixed; right
   now it's dead code from the client's perspective.
2. **No CORS on `services/api`.** No `Access-Control-Allow-Origin` on any
   response, and an OPTIONS preflight to `/cases` returns a bare 405.
   Harmless for this PR — `app/api/proxy/[...path]/route.ts` makes the real
   call server-to-server, which isn't subject to CORS — but worth knowing if
   any other consumer ever wants to call the API directly from a browser.
3. **`POST /cases` and manual intake — resolved.** Previously a HANDOFF item
   here; §3.3 now has it and it works as documented
   (`curl -X POST {API}/cases -d '{"patient":{...},"bill":{...}}'` →
   `{"case_id": "..."}`). `lib/api.ts`'s `createCase()` now calls it for
   real.
4. **`events[].agent` "verifier" and `Case.denial_flag` — resolved, but
   worth reconciling the playbook text.** Both were HANDOFF items here;
   SWARM's `services/agent-core/agent_core/pipeline.py` independently
   reached the same shapes CANVAS's `lib/types.ts` already used
   (`denial_flag: {violated, reason, citation} | null`, richer than §3.1's
   amended `bool`, with a matching comment explaining why) — no code change
   needed on either side, they already agree. Still worth a §3.1 text update
   so a reader doesn't hit the same "wait, which is it" moment twice.
5. **Minor undocumented/looser live shapes**, all handled defensively in
   `lib/types.ts` rather than blocking on a contract fix: `Bill.hospital_ccn`
   is missing on some cases and `""` on others (never rendered, so harmless);
   `Bill.has_itemized_bill` and `CaseDocument.raw_text` appear on every live
   response but aren't in §3.1 (the latter is now shown in the document
   gallery — it's the actual synthetic bill text, a nice demo touch);
   `Hospital.ccn` and `CaseDocument.gcs_uri` come back `null` rather than the
   `string` §3.1 implies.
6. **`infra/deploy.sh` doesn't wire `ef-web`'s `API_BASE_URL`.** It already
   resolves `ef-agent-core`'s URL for `api`'s `AGENT_CORE_URL`
   (`deploy_one()`'s `extra_env` block) — the same pattern (resolve
   `ef-api`'s URL, set it as `web`'s `API_BASE_URL`) would make
   `./infra/deploy.sh web` wire itself to the live API with no manual
   `gcloud run services update` step after. Not made here since `infra/` is
   outside `web/`'s owned paths (§0.2) — proposing it for ATLAS.

## What's not done (by design, not oversight)

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
