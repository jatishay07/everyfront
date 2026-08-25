# Every Front

**A multi-agent system that finds every legal front to reduce or erase a medical bill — and files the paperwork itself.**

A medical bill lands in an inbox. Every Front identifies the hospital, works out
every statute the bill can be challenged on, computes every deadline those
statutes impose, fills the real forms, and delivers them by fax and certified
mail — with a human approving every filing before it goes out.

Built for Google's **All Things Agentic Hackathon**, track **The Taskmaster**.

---

## The problem

Charity care is a legal entitlement most eligible patients never use, and a bill
that's wrong is rarely caught:

- **76%** of patients eligible for hospital charity care never applied, because
  they didn't know it existed.
- **Under 1%** of claim/billing denials are ever appealed — against a roughly
  **34%** success rate when someone does.
- Roughly **$14B a year** in charity care that hospitals are required to make
  available goes unawarded.

These are the commonly-cited figures behind the "nobody appeals, nobody applies"
problem this project targets (patient-advocacy and health-policy reporting on
charity care and claims denials, e.g. Dollar For, KFF, and Commonwealth Fund
coverage of 501(r) and appeals behavior). We have not independently re-verified
these three market-level numbers the way we verified the technical claims below —
see [Honest limitations](#honest-limitations).

What *is* independently verified, against primary sources, is in
[`docs/SPIKE.md`](docs/SPIKE.md):

- We reconstruct a hospital's charity-care policy — thresholds and application
  URLs — directly from its IRS Form 990 Schedule H XML filing. No third-party
  database; the source is the tax filing itself. That pipeline now runs for
  real: **200 hospitals seeded to Firestore**, **100% live FAP URL rate**
  (see [`packages/datapipes`](#whats-actually-live-right-now) below).
- We reach hospitals' CMS-mandated machine-readable price files and extract
  their attested cash price. **Advocate Christ Medical Center** bills **$140**
  for CPT 86787 and accepts **$70** cash from a self-pay patient — a flat
  50%-of-gross discount. A patient billed the gross rate is being overcharged
  **2×**, and the hospital's own published file proves it.
- The system is live on Cloud Run today, and the full pipeline runs end to
  end — not just a seed agent. See [The pipeline works end to
  end](#the-pipeline-works-end-to-end-a-real-run) for an actual, unedited
  transcript.

---

## Why this needs agents, not a form

A single bill can put five independent legal clocks in motion at once, each
started by a different trigger date, each with different consequences for the
others:

| Clock | Starts on | Window |
|---|---|---|
| Charity-care application (26 CFR 1.501(r)-4) | first **post-discharge billing statement** — not the date of service | 240 days (federal floor; some states are longer, some have none, Illinois runs from the *latest* of several events) |
| Extraordinary collection action moratorium (26 CFR 1.501(r)-6) | same statement | 120 days, during which the hospital cannot escalate collections |
| Patient-Provider Dispute Resolution (45 CFR 149.620) | initial bill | 120 calendar days — only if uninsured/self-pay and the bill exceeds the Good Faith Estimate by ≥$400 |
| Debt validation (12 CFR 1006.34 / 15 USC 1692g) | validation notice from a collector | 30 days — and it must be resolved *before* other fronts, because it freezes collection |
| Itemized-bill / billing-audit request (42 USC 1395b-7(b)) | request for an itemized bill | 30 days for the hospital to produce it |

Getting the *ordering* right is as important as getting each deadline right: a
case in collections has to hit debt validation first because it's the one that
freezes everything else. That sequencing, plus the fact that every one of
these clocks depends on facts extracted from an unstructured document (a
scanned bill, a denial letter, a collection notice), is what turns this from a
form-fill problem into a genuine multi-agent problem: a **Reader** has to
extract the fact, a **Clock** has to run the math no LLM is trusted to run,
and a **Strategist** has to sequence the result.

---

## Architecture

![Architecture diagram](docs/architecture.svg)

**Six agents**, run inside a single Google ADK agent hierarchy on Cloud Run —
all six execute on every case today, not just the three that used to be
architected-but-unwired:

| Agent | Job |
|---|---|
| **Reader** | Classifies an incoming document with **Gemma 4** (`gemma-4-26b-a4b-it`) as the first pass, then extracts structured fields with **Gemini 3.7 Flash** — temperature 0, JSON-schema output. Both models are called on every real document, not a fallback path. |
| **Lookup** | Resolves the hospital from the bill's EIN against the Firestore `hospitals/{ein}` record LEDGER's pipeline seeded from IRS filings, and is honest when a hospital is for-profit and owes no 501(r) duty at all. *Resolution today is EIN-only — see [Honest limitations](#honest-limitations).* |
| **Clock / Auditor** | A thin LLM wrapper around the deterministic rules engine. **The LLM narrates, the code computes** — every deadline carries its regulation citation, verified live (see the transcript below). The audit half calls the same engine for NCCI/duplicate/cash-price findings, but the dollar amount it reports is currently $0 in production — also detailed in limitations. |
| **Strategist** | Picks and sequences the applicable fronts, and waits for a human to click **Approve** before any filing goes out — enforced in plain Python control flow, not left to an LLM's discretion. |
| **Verifier** | Cross-checks extracted income documents against the patient's stated income and household size before a filing is allowed to fire; blocks on mismatch with a plain-English reason. |
| **Filer** | Renders the filled PDF, sends it (fax or certified mail), and records delivery proof. |

**Google Cloud, by name:**

- **Cloud Run** hosts every service — intake webhook, agent-core, the API, and
  the dashboard — deployed by `infra/deploy.sh all`, and scales to zero
  between demos.
- **Pub/Sub** is the event backbone: `intake.email.received` →
  `case.document.added` → `case.analysis.complete` → `filing.requested` →
  `filing.completed`, each with a dead-letter topic so a poison message can't
  loop forever.
- **Firestore** holds all case state: `cases/`, `hospitals/`, `filings/`, and
  the `events/` subcollection that is the audit log the dashboard's activity
  feed reads from.

**External services:** Gmail API (intake, `users.watch` → Pub/Sub), IRS Form
990 Schedule H bulk XML and CMS hospital price-transparency files (data),
Phaxio and Lob in test mode with a hard destination allowlist enforced in code
(delivery), Google Calendar and Drive (deadline tracking, per-case folders).

---

## The pipeline works end to end — a real run

This is not a description of intended behavior. On 2026-08-25 we called the
live API directly:

```
curl -X POST https://ef-api-756591166292.us-central1.run.app/demo/inject_bill \
  -H "Content-Type: application/json" \
  -d '{"fixture_name":"case_01_uninsured_gfe_ca"}'
```

**HTTP 200 in 74.5 seconds.** The response carried a real case ID
(`demo-case_01_uninsured_gfe_ca-86412e24`); pulling `GET /cases/{id}` back
showed:

- **30 events** logged to `cases/{id}/events`, each with an agent name and a
  citation — Reader classifying via Gemma, Lookup resolving Sutter Bay
  Hospitals as nonprofit (`26 CFR 1.501(r)-1(b)(29)(i)`), Clock computing three
  deadlines, Auditor running (and honestly skipping the denial check — no
  denial letter on this case), Strategist selecting fronts.
- **4 fronts evaluated**, all with reasons and citations:
  - `audit` — applicable (itemized bill on file)
  - `charity_care` — applicable, **income at 117.13% of FPL**, under the
    400% free-care threshold (`26 CFR 1.501(r)-4(b)(2)`; Cal. HSC §127405)
  - `debt_validation` — correctly marked **not** applicable ("account is not
    reported in collections")
  - `ppdr` — applicable, **deadline computed as 2026-10-03**, "$700.00 above
    the Good Faith Estimate (>= $400 floor)" (`45 CFR 149.620(b), (c)`)

This was one real fixture out of the 8 in `fixtures/`, run against the live
`ef-api` and `ef-agent-core` Cloud Run services, not a mock. It's also the
run that surfaced the `savings_found_cents: 0` and `audit_findings_cents: 0`
result documented honestly below — we're showing the real output, including
the part that doesn't work yet.

Processing time: this run took 75 seconds for a 3-document case. Other cases
in the corpus have taken longer — on the order of a couple of minutes per
case is typical right now (agent-core is not yet latency-optimized). The demo
video is scripted around this.

---

## What's actually live right now

Every persona has shipped and merged. This table is what we verified
ourselves against the repo and the live services, not a status report handed
to us.

| Component | Status |
|---|---|
| `infra/setup.sh`, `infra/deploy.sh` | **Live.** One-command bootstrap (APIs, Firestore, GCS, Pub/Sub topics + subscriptions, service accounts) and deploy of all four services (`intake`, `agent-core`, `api`, `web`) to Cloud Run. |
| `packages/rules` | **Live.** The complete legal engine — deadlines, eligibility, front selection, billing audit, denial triage. We ran the suite ourselves: **182 tests, 100% branch coverage**, zero LLM calls anywhere in the package, every rule cites its regulation in its docstring. |
| `packages/datapipes` | **Live.** 200 real hospitals seeded to Firestore from IRS Schedule H XML filings, 100% live FAP URL rate (by construction — selecting for a repairable URL rather than sampling at random, per `docs/SPIKE.md`'s finding); EIN↔CCN crosswalk resolved for 180/200; 2,881 NCCI PTP pairs and 15,112 MUE codes loaded; MRF fetcher pulling real attested cash prices from 3 live hospital systems. We independently confirmed one seeded record live: `GET /hospitals/94-0562680` on the deployed API returns Sutter Bay Hospitals with `free_care_max_fpl_pct: 400`, matching `docs/SPIKE.md`'s finding exactly. |
| `services/agent-core` | **Live on Cloud Run**, running the full six-agent ADK hierarchy (not the earlier hello-world seed) via Gemini 3.7 Flash + Gemma 4 over Vertex AI. Verified with a real `/demo/inject_bill` call — see above. |
| `services/api` | **Live on Cloud Run.** All eight §3.3 endpoints implemented, including `POST /demo/inject_bill` and `GET /events`. |
| `services/intake`, `packages/delivery` | **Built.** Gmail `users.watch` → GCS → Pub/Sub intake; five real PDF forms filled (the actual CMS PPDR dispute form, Sutter's and Advocate's own FAP applications, plus two generated letters); Phaxio/Lob fax and mail behind one swappable interface with a hard NANP/ZIP allowlist in code; Calendar and Drive sync. *Fax/mail sends fall back to a recording stub without live vendor credentials — see limitations.* |
| `web` | **Built.** Four routes: command center (stats banner, case list), case detail (event timeline, fronts panel, deadline ladder, Approve button), live activity feed, intake flow. Deployed via `infra/deploy.sh web`. |
| `fixtures` | **Live.** 8 synthetic patients with real generated bill/GFE/denial-letter/collection-notice PDFs, covering PPDR+charity, wrongful denial, in-collections ordering, for-profit honesty, a cat-photo upload, and an unparseable bill. `make demo-reset && make demo-run` drives the corpus end to end. |

---

## One-command spin-up

This is a hard requirement we take seriously: a fresh GCP project should reach
public, working Cloud Run URLs with **no console clicks**.

```bash
# 1. Bootstrap the project: enables APIs, creates Firestore, GCS buckets,
#    Pub/Sub topics + subscriptions, and least-privilege service accounts.
#    Idempotent -- safe to run again if anything fails partway through.
PROJECT_ID=<your-project-id> BILLING_ACCOUNT=<your-billing-account-id> ./infra/setup.sh

# 2. Build and deploy every service to Cloud Run: intake, agent-core, api, web.
./infra/deploy.sh all
```

Prerequisites: the [`gcloud` CLI](https://cloud.google.com/sdk/docs/install)
installed and authenticated (`gcloud auth login`), and a billing account you
can link (`gcloud billing accounts list`). Nothing else is required locally —
`deploy.sh` builds containers via Cloud Build, not your machine.

Deploy a single service instead of everything:

```bash
PROJECT_ID=<your-project-id> ./infra/deploy.sh agent-core
```

`deploy.sh` pins the Vertex AI endpoint to `location=global` deliberately —
`us-central1` does not serve Gemini 3.x and silently falls back to a model
below the hackathon's "Gemini 3.5 or newer" bar. See `docs/SPIKE.md` for how
that was caught.

Once deployed, drive the whole system the way we did above, without waiting
on Gmail:

```bash
curl -X POST https://<your-api-url>/demo/inject_bill \
  -H "Content-Type: application/json" \
  -d '{"fixture_name":"case_01_uninsured_gfe_ca"}'
```

Full variable reference: [`.env.example`](.env.example). Deeper infra notes:
[`infra/README.md`](infra/README.md).

---

## Repository layout

```
everyfront/
├── infra/          # setup.sh, deploy.sh, service configs
├── services/
│   ├── intake/     # Gmail push webhook → Pub/Sub → GCS
│   ├── agent-core/ # the ADK agent hierarchy: Reader, Lookup, Clock/Auditor,
│   │               # Strategist, Verifier, Filer
│   └── api/        # FastAPI REST for the dashboard (all 8 §3.3 endpoints)
├── packages/
│   ├── rules/      # deterministic legal engine (deadlines, eligibility,
│   │               # front selection, billing audit, denial triage)
│   ├── datapipes/  # IRS/CMS/NCCI/FPL data pipelines, seeded to Firestore
│   └── delivery/   # fax, certified mail, PDF fill, calendar, Drive
├── web/            # Next.js dashboard — 4 routes
├── fixtures/       # synthetic patient corpus, watermarked "SYNTHETIC — DEMO"
├── tests/          # unit + contract + e2e tests
└── docs/           # architecture diagram, video script, docs/SPIKE.md
```

Every legal rule in `packages/rules` cites its regulation section in its
docstring, and the deadline math has no LLM calls anywhere in the package —
see [Working Agreement §2.1](BUILD_PLAYBOOK.md#2-non-negotiable-working-agreements)
if you want the full reasoning.

---

## Honest limitations

We would rather a judge learn these from us than discover them independently.
The first five are structural or unresolved by design; the next three are
specific, verified gaps in the current build that other work is closing in
parallel:

- **Synthetic data only.** Every patient, bill, and case in this repo and its
  demo is fabricated for the purpose. No real name, SSN, or real patient bill
  exists in this codebase (see `fixtures/README.md`).
- **No HIPAA compliance.** This is a hackathon prototype. It is not a covered
  entity, has not undergone a security or privacy assessment, and should not
  handle real patient data as built.
- **Roughly 40% of U.S. hospitals are for-profit** and owe **no 501(r)
  charity-care duty at all.** The charity-care front simply does not apply to
  them, and the system is designed to say so explicitly (`nonprofit: false` →
  no charity-care front) rather than pretend a right exists where it doesn't.
- **CMS does not publish Patient-Provider Dispute Resolution volumes or
  outcomes.** We can cite the statute and the 120-day window with confidence;
  we cannot cite a public success rate for PPDR the way we can for charity-care
  appeals generally, and we don't claim one.
- **The 76% / <1%-vs-34% / $14B figures are widely cited, not independently
  re-derived by this project.** Everything under "What is independently
  verified" above, by contrast, was checked against a primary source ourselves
  and the evidence is committed in `docs/spike/`.
- **The savings/audit-findings figure is $0 in the live system today**, and we
  reproduced this ourselves: the fixture we ran above was seeded with a
  duplicate line item and an NCCI-flagged code, and the real run still
  returned `audit_findings_cents: 0`. The root cause is in the extraction
  handoff, not the rules engine — Reader's Gemini extraction schema for a
  bill currently returns only aggregate fields (`amount_cents`,
  `service_date`, dates), not a structured `line_items` array, so
  `audit_line_items()` never receives anything to check. `packages/rules`'
  audit function itself is fully implemented and unit-tested at 100% branch
  coverage (see its tests) — it's what Reader hands it that's incomplete.
  Separately, the MRF cash-price comparison isn't yet threaded from Lookup
  into Auditor even though the underlying fetcher works (`docs/SPIKE.md`).
  Both are in progress.
- **Hospital resolution is EIN-only.** Lookup resolves a case's hospital by
  reading `bill.hospital_ein` and looking up `hospitals/{ein}` in Firestore.
  There is no fallback that matches on hospital name text, so a document
  Reader can't pin an EIN to produces an honest "not resolved" note rather
  than a guess. Fixing this is in flight.
- **Vendor sends fall back to a recording stub.** Phaxio and Lob are wired
  behind one interface with a hard fax/ZIP allowlist enforced in code
  (never a real hospital destination), but without live vendor credentials
  configured, both clients fall back to `FakeFaxVendor`/`FakeMailVendor` —
  a recording stub that returns realistic vendor IDs and status, but never
  actually reaches Phaxio or Lob. No filing in this repo has gone out
  through a live vendor account yet.
- **This is a 12-day build in active progress.** The status table above is the
  actual state of the repo as of 2026-08-25, not a roadmap dressed up as a
  demo.

---

## License

[MIT](LICENSE).
