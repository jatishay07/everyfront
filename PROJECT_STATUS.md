# EVERY FRONT — Full Status Report

**Written 2026-08-26 by FORGE (CTO persona).** Every claim here was verified against the
deployed system or the repository, not taken from an agent's report. Where something is
unproven, it says so.

Companion documents: `BUILD_PLAYBOOK.md` (the spec — personas, contracts, work orders),
`HANDOFF.md` (quick-start for a fresh session), `docs/SPIKE.md` (day-1 feasibility evidence).

---

# PART 1 — What this was supposed to be

## The product, in one sentence (§1.1)

> A medical bill lands in a Gmail inbox; an ADK multi-agent system identifies the
> hospital, determines every legal front on which the bill can be reduced or erased,
> computes every statutory deadline, fills the real forms, and delivers them by fax and
> certified mail — autonomously, across a caseload.

## Why it should exist

Roughly 76% of people eligible for hospital charity care never apply, because they do not
know it exists. Under 1% of insurance denials are appealed, against a success rate near
34% when they are. An estimated $14B/yr in charity care goes unawarded.

The friction is not that the law is unavailable. It is that four separate legal regimes
each have their own eligibility test, their own deadline, and their own form — and the
deadlines run from *different trigger dates* that a patient has no reason to have recorded.

## The four legal fronts (§1.2)

| Front | Authority | Clock | What the system does |
|---|---|---|---|
| **Charity care** | 26 CFR 1.501(r)-4, -6 | ≥240 days from the **first post-discharge billing statement** (not the service date) | Look up the hospital's own FAP and thresholds from its IRS Schedule H, screen income vs FPL, fill the hospital's own application |
| **PPDR** (bill vs estimate) | 45 CFR 149.620 | 120 calendar days from the initial bill; delta ≥ $400 vs Good Faith Estimate; uninsured/self-pay | Fill the real CMS dispute form; filing freezes collections and late fees |
| **Debt validation** | 12 CFR 1006.34 / 15 USC 1692g | 30 days from the validation notice | Generate a written dispute by certified mail; the collector must cease collection until it produces verification |
| **Billing audit** | 42 USC 1395b-7(b); 45 CFR Part 180 | itemized bill due 30 days after request | NCCI unbundling and unit checks; compare billed charges against the hospital's own attested cash price from its published file |

The key legal lever the product encodes: **26 CFR 1.501(r)-4(b)(3)** — a hospital may not
deny financial assistance for missing documentation its own published policy does not
list. About 24% of charity-care denials are "paperwork." The denial-triage feature
cross-checks demanded documents against the published list.

## The architectural bet (§2.1)

> **Deterministic core, LLM shell.** All deadline math, eligibility math, NCCI checks and
> front-selection logic live in `packages/rules` as pure, unit-tested Python. LLMs
> classify, extract, and draft — they never compute a deadline.

Every legal rule cites its source in the docstring: regulation section and effective date.
The stated goal was that a judge reading the repo finds *the law as code, with citations*.

## Hard hackathon requirements (§1.3 — Stage One is pass/fail)

- Gemini 3.5 or newer · at least one Google agent framework (ADK) · at least one of
  Cloud Run / Cloud SQL / Firestore / GKE / Pub/Sub
- Public repo with spin-up instructions · architecture diagram · ~4-minute demo video
  showing live execution **with visible proof of Google Cloud deployment** · hosted URL
- Judging: Innovation & Operational Utility 40% · Architectural Discipline 30% ·
  Demo & Production Readiness 30% · bonus up to 0.6

## How it was to be built (§4)

Nine personas, each owning disjoint directories, communicating only through the contracts
in §3: **FORGE** (CTO), **ATLAS** (platform), **LEDGER** (data), **STATUTE** (rules),
**RELAY** (integrations), **SWARM** (agents), **CANVAS** (frontend), **PROOF** (QA),
**MEGAPHONE** (submission).

---

# PART 2 — What it is now

**30 PRs merged · 46 commits · 513 tests passing · 4 services live on Google Cloud.**

```
Dashboard    https://ef-web-756591166292.us-central1.run.app
API          https://ef-api-756591166292.us-central1.run.app
Agent core   https://ef-agent-core-756591166292.us-central1.run.app
Intake       https://ef-intake-756591166292.us-central1.run.app
GCP project  everyfront-hack-2026 (us-central1) · budget $150, alerts at 33/66/90/100%
```

## What actually works, with the evidence

### The legal engine — the strongest part
`packages/rules` — **100% branch coverage, 512 statements, 194 branches, zero LLM calls.**
Five public functions per contract §3.5: `compute_deadlines`, `screen_eligibility`,
`select_fronts`, `audit_line_items`, `check_denial_lawfulness`. Every rule cites its
regulation; every result carries `.explain()` showing the arithmetic.

Constants were audited against primary sources, which corrected real errors:
- The 240-day window was cited to 26 CFR 1.501(r)-4(b)(1)(iv), which *requires the policy
  to describe* the application period. The period is *defined* at 1.501(r)-1(b)(3). The
  definition is the correct authority for the number.
- 2025 Alaska and Hawaii FPL increments were each $10 low (90 FR 5917).
- **Illinois was materially misread in the spec.** §4 lists IL as "90 days," which invited
  encoding it as a shorter charity-care window. It is not: 210 ILCS 89/10 is a *separate*
  state entitlement (uninsured, ≤300% FPL, 90 days from discharge) that runs *alongside*
  the federal 240-day window. Encoding it as an override would have told patients their
  federal right expired ~150 days before it did. It also binds for-profit hospitals, which
  the "no 501(r) obligation" honesty path would otherwise have written off.

### Real hospital data
`packages/datapipes` — **204 hospitals seeded to Firestore from real IRS Form 990
Schedule H XML**: thresholds, FAP URLs, CCNs, MRF pointers. Plus 2,881 real NCCI PTP pairs
and 15,112 MUE codes in a bundled sqlite, sub-millisecond lookup.

Discovered along the way: only 31.8% of Schedule H line-16a values are usable URLs (66.6%
are cross-references like "SEE PART V, SECTION C"); a scheme/colon repair pass lifts that
to 61.2%; 83% of repaired URLs resolve live. So the seed *selects* hospitals with usable
URLs rather than sampling — random sampling yields ~26% and fails the ≥60% bar.

### Finding the overcharge
The audit finds real, substantiated overcharges. The flagship, provable from the
hospital's own CMS-mandated published file:

> **Advocate Christ Medical Center bills $140.00 for CPT 86787 and accepts $70.00 cash.**
> A self-pay patient billed gross is demonstrably overcharged 2×.

`total_savings_cents` credits each line its *largest* substantiated theory rather than
summing, because a duplicate-line finding and a cash-price finding on the same line would
otherwise double-count. Savings are `max(audit findings, charity-care erasure)`, not the
sum, because a granted free-care determination erases the whole bill and already subsumes
any billing errors on it. **Over-claiming here would be worse than under-claiming.**

### Filing real paperwork
Five real forms render and file live: the **actual CMS PPDR initiation form**, **Sutter's
and Advocate's own FAP applications**, and generated debt-validation and records-request
letters. Verified by downloading one from GCS and opening it:

```
188,751 bytes · %PDF- · 4 pages · Sutter Bay Hospitals' real FAP application
  pat_name = Jordan Alvarez · family_size = 3 · income_total_pat = 2,666.67
```

### The safety behaviour that is arguably the best demo beat
The **Verifier refuses to file** on incomplete evidence — verified live on two cases: one
with no income document, and one where the submitted "income proof" is a **photograph of a
cat**. An agent declining to file bad paperwork on a patient's behalf is the human-in-the-
loop moment §4 says judges reward.

Equally: on a bill it cannot read, the system now returns **absent fields and files
nothing**, with all four fronts marked not-applicable and a plain reason for each. It
declines rather than guessing.

### The rest
- **Human-in-the-loop gate** — `POST /cases/{id}/approve_filing`, returns in ~3.5s
- **Event backbone** — Pub/Sub push with OIDC; `filing.requested` → agent-core
- **Dashboard** — live on real data, Lighthouse 100 / 95 / 96 / 100
- **Infrastructure** — one idempotent command builds the project: APIs, Firestore, buckets,
  topics, subscriptions, service accounts, indexes, budget alerts, scheduler jobs
- **CI** — lint, format, tests, plus a secret/PHI scan (the repo is public; synthetic data only)

## The live demo banner

```
8 cases · 4 hospitals · 1 deadline this week · $13,805 billed
5 charity-eligible · 2 PPDR-eligible · 1 unlawful denial flagged
$2,922.50 in billing errors found · 12 filings sent · 0 human hours
```

Every figure derives from the 8 seeded cases, and PROOF's live reconciliation test
recomputes all ten from `GET /cases` and diffs them against the deployed handler —
3/3 passing. Earlier it read `5 hospitals`, `6 charity-eligible`, `11 filings` — **the
numbers got smaller because they got true.** `filings_sent` then went 7 → 12, because
fixing the `fronts[]` defects let every approved front actually reach `filed` instead
of losing its status to a sibling write.

---

# PART 3 — The defect pattern (the most transferable finding)

**Every serious defect in this project reported success while doing nothing.** None
crashed. All looked correct. The test suite was green throughout. Each was found only by
deploying and reading the actual output.

| # | Defect | Why nothing caught it |
|---|---|---|
| 1 | The Filer filed five-line text placeholders for weeks | SWARM guessed `delivery.fax`; RELAY shipped `delivery.vendors.fax`. A `try/except ImportError` swallowed the mismatch and reported `"status": "sent"` |
| 2 | All five Pub/Sub subscriptions were PULL with no subscriber | `approve_filing` published into a queue nobody read. `setup.sh`'s own comment promised `deploy.sh` would convert them; it never did |
| 3 | `deploy.sh --source=services/<name>` uploaded only that directory | Any service importing `packages/rules` built clean, passed every test, died at runtime |
| 4 | The container had `packages/delivery`'s code but not its dependencies | Every real filing 500'd on `ModuleNotFoundError: pypdf` — the placeholder never needed it |
| 5 | The Reader **fabricated** an EIN and epoch dates on an unreadable bill | The Clock then computed deadlines carrying real citations, and the Filer mailed a letter to "unknown hospital" |
| 6 | Every filing was reported as a **live send** | `send_filing()` never set the `simulated` flag the Filer reads |
| 7 | Every letter went out **blank** | Templates read `patient["first_name"]` / `["address"]` — fields that do not exist in the contract |
| 8 | `GET /events` returned 500 on every request | A missing Firestore collection-group index. That endpoint is the live activity feed — the demo's centrepiece |
| 9 | The rules engine was **duplicated** in `agent-core` | A `try/except` fallback carried its own copy of a bug STATUTE had fixed upstream, so the fix never reached production |
| 10 | Concurrent filings **clobbered each other's status** via a non-atomic read-modify-write of the whole `fronts[]` array | Every write succeeded. The lost one was simply overwritten a second later, and only a cross-reference against `filings/` showed a front open with a real sent filing behind it |
| 11 | **Re-analysis reopened filed fronts.** The Filer stores its PDF as a case document → republishes `case.document.added` → re-runs analysis → `select_fronts` returns "open" | Both writers were correct in isolation. The feedback loop only exists once filing writes a document, and no transaction can fix it |
| 12 | Writes **resurrected purged cases**: `.set(merge=True)` creates a missing document, so a late write after a delete brought a case back with no `patient`, no `bill`, no `created_at` | A zombie case looks like a real one in `GET /cases` and on the dashboard |

**The rule that follows: never trust a green test suite, or an agent's report about
runtime behaviour. Deploy, curl the real endpoint, read the actual bytes.**

Four agent claims were wrong in a single day — two overclaimed, and two *underclaimed*
because they measured a deployment three commits stale. **Always redeploy before believing
a live measurement.**

A second lesson: the defects were **seams, not components.** Each persona's work was good
in isolation. What broke was where two of them met, and it broke quietly because the
fallbacks were written to be forgiving. Forgiving fallbacks hide integration failures.

---

# PART 4 — What is left

## Blocking — cleared 2026-08-26

**The `fronts[]` lost-update race is fixed, deployed and verified live.** PROOF's
diagnosis was right and incomplete: the symptom had three independent causes
(defects 10, 11 and 12 above), and only the first was the race it named. Fixing the
race alone left ef-2026-0007 still showing two fronts open with real sent filings
behind them — which is how the other two were found.

Evidence, measured against the deployed system rather than the test suite:

| | before | after |
|---|---|---|
| Fronts inconsistent with their own `filings/` records | 5, across ef-2026-0001/-0003/-0007 | **0** |
| Stray non-corpus cases in `GET /cases` | 1 | **0** |
| `demo-reset && demo-run` | exit 1, `BLOCKED: not every approved front reached status=filed` | **exit 0, twice consecutively** (120.3s and 129.4s of a 240s budget) |
| Live banner reconciliation | — | **3/3 pass** |

A control run pinned the mechanism down rather than assuming it: four concurrent
writers against one real Firestore case lost **3 of 4** updates through the old
read-modify-write, and **0 of 4** through the transaction.

Each cause carries a regression test verified to fail against the pre-fix code —
including one that drives two `finalize_filing` coroutines through an
`asyncio.Barrier` so the interleaving is deterministic instead of lucky.

Also closed: CI was running neither service's test suite (root `testpaths` is
`["tests", "packages"]`), so every regression test for a defect that only ever
appeared in a deployed service sat there unrun. Both suites are wired in now, along
with `fixtures/requirements.txt`, which PROOF had flagged twice.

## Demo and submission

- [x] `make demo-reset && make demo-run` passing **twice consecutively** (§4 persona 7
      acceptance) — exit 0 both times, 120.3s and 129.4s of a 240s budget, live on
      2026-08-26 against revision `ef-agent-core-00025-k54`
- [ ] Three full rehearsals (§5). Runbook is in `fixtures/DEMO_CHECKLIST.md` with beat-by-beat
      timings and an "if this beat runs long" column
- [ ] **Record the ~4-minute video.** Must show live execution *and* visible proof of Google
      Cloud deployment — a §1.3 hard requirement, so cut to the Cloud Run console.
      Recommended beats: `case_01` as the live flagship (most reliable), `ef-2026-0005`
      (the cat photo rejection) as a 15-second zero-risk cutaway, `ef-2026-0007` for the
      audit findings, `ef-2026-0002` for the unlawful-denial flag
- [ ] **Submit to Devpost by Aug 30**, not Aug 31 — 24h buffer against upload failures.
      Draft in `docs/DEVPOST_SUBMISSION.md`
- [ ] Verify the video is public, not "Made for Kids", under length, in English
- [ ] Bonus 0.6: blog 0.2 · social with #AllThingsAgenticHackathon 0.2 · Gemma 0.2
      (Gemma does real first-pass classification — name it explicitly)

## Built but not wired

| Gap | State | To finish |
|---|---|---|
| **Gmail intake** | Cloud Scheduler renewal wired and the push subscription is correctly PUSH — but the code path is **not** complete, and this row previously claimed "auto-create fixed", which was false. Nothing in `agent-core` creates a case from a `case.document.added` event (`grep -rn create_case services/` finds it only in `services/api`), so an emailed bill reaches GCS, returns 200 at every hop, and produces no case. The Gmail push topic also lacks the `gmail-api-push@system.gserviceaccount.com` publisher binding `users.watch` requires. **No OAuth token minted** | Three fixes in flight (2026-08-26), then a human at a Google consent screen. Runbook in `infra/OAUTH.md` — note its step-6 verification only checks that the PDF reached GCS, which passes even when the feature is dead |
| **Calendar + Drive** | Built and tested in `packages/delivery`, never called by the pipeline | RELAY left exact call sites in PR #35's handoff |
| **Real fax / mail** | Interface live; vendors are recording stubs | Phaxio and Lob offer free test keys; signup needs a human |
| **`case.analysis.complete`, `filing.completed`** | PULL subscriptions, no handlers | Informational — Firestore state is written synchronously before publish |
| **`fixtures/requirements.txt`** | Not installed in CI | One line in the workflow |

## Deliberately not done

**No authentication on any endpoint.** Anyone with the URL can read every case and approve
filings. For a synthetic-data demo where judges must click a live link, adding auth would
make the demo worse. It is documented as a stated choice in `infra/README.md` and the
README's limitations section — **not an oversight, and indefensible the moment real data
appears.**

---

# PART 5 — Honest assessment

**The analysis half is genuinely strong.** The legal engine is deterministic with 100%
branch coverage, every rule verified against a primary source, and the hospital data is
real — reconstructed from IRS filings and CMS price-transparency files rather than mocked.
The $140-vs-$70 finding is provable from a document the hospital published itself.

**The action half works and has been demonstrated end to end** — a real filled form,
stored and filed — but it took finding twelve silent failures to get there. The last
three were all one symptom with three unrelated causes, and each was found only by
deploying the previous fix and reading the live data rather than the test output.

**What would need to be true before a real patient's bill went through this:**
authentication, a real fax and mail path with delivery receipts, HIPAA handling, and a
human reviewing every filing before it leaves.

**As a hackathon submission** it has a defensible core, real public data, verifiable
citations, an agent that refuses to act on incomplete evidence, and a limitations section
that is accurate rather than flattering. That honesty is a scoring asset under §4 persona
8, not a liability — and it is the part I would least want edited out.
