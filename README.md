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
  database; the source is the tax filing itself.
- We reach hospitals' CMS-mandated machine-readable price files and extract
  their attested cash price. **Advocate Christ Medical Center** bills **$140**
  for CPT 86787 and accepts **$70** cash from a self-pay patient — a flat
  50%-of-gross discount. A patient billed the gross rate is being overcharged
  **2×**, and the hospital's own published file proves it.
- The system is live on Cloud Run today:
  **https://ef-agent-core-756591166292.us-central1.run.app**, running Gemini
  3.7 Flash via Vertex AI.

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

**Six agents**, run inside a single Google ADK agent hierarchy on Cloud Run:

| Agent | Job |
|---|---|
| **Reader** | Classifies an incoming document (Gemma 4, first pass), then extracts structured fields with Gemini 3.7 Flash — temperature 0, JSON-schema output. |
| **Lookup** | Resolves the hospital from EIN/CCN, pulls its charity-care policy and cash prices out of Firestore, and is honest when a hospital is for-profit and owes no 501(r) duty at all. |
| **Clock / Auditor** | A thin LLM wrapper around the deterministic rules engine. **The LLM narrates, the code computes** — every deadline and every audit finding is produced by pure Python and carries its regulation citation. |
| **Strategist** | Picks and sequences the applicable fronts, and waits for a human to click **Approve** before any filing goes out. |
| **Verifier** | Cross-checks extracted income documents against the patient's stated income and household size before a filing is allowed to fire; blocks on mismatch with a plain-English reason. |
| **Filer** | Renders the filled PDF, sends it (fax or certified mail), and records delivery proof. |

**Google Cloud, by name:**

- **Cloud Run** hosts every service (intake webhook, agent-core, the API, the
  dashboard) and scales to zero between demos.
- **Pub/Sub** is the event backbone: `intake.email.received` →
  `case.document.added` → `case.analysis.complete` → `filing.requested` →
  `filing.completed`, each with a dead-letter topic so a poison message can't
  loop forever.
- **Firestore** holds all case state: `cases/`, `hospitals/`, `filings/`, and
  the `events/` subcollection that is the audit log the dashboard's activity
  feed reads from.

**External services:** Gmail API (intake), IRS Form 990 Schedule H bulk XML and
CMS hospital price-transparency files (data), Phaxio and Lob in test mode
(delivery), Google Calendar and Drive (deadline tracking, case folders).

### What's actually live right now vs. what's still being built

This is a 12-day build, in progress. The README is written to be accurate on
the day it's read, not aspirational:

| Component | Status |
|---|---|
| `infra/setup.sh`, `infra/deploy.sh` | **Live.** One-command project bootstrap and deploy, verified end-to-end against a real GCP project (see below). |
| `services/agent-core` seed agent | **Live on Cloud Run**, calling Gemini 3.7 Flash via Vertex AI and a real deterministic tool (`compute_fap_deadline`). This is the seed the full six-agent hierarchy is being built on top of — it is not yet the full hierarchy. |
| `packages/rules` (deadline engine, eligibility screen, FPL tables) | **Implemented and unit-tested**, with citations in every docstring. `select_fronts`, `audit_line_items`, and `check_denial_lawfulness` are contracted in the playbook but not yet written. |
| IRS Schedule H parser, CMS price-file fetcher | **Proven feasible and documented** with real extracted data in `docs/SPIKE.md` and `docs/spike/`. The production seeding pipeline (`packages/datapipes`) that populates `hospitals/` at scale is still being built. |
| Gmail intake, PDF fill, fax/mail delivery (`services/intake`, `packages/delivery`) | Not yet built. |
| REST API, dashboard (`services/api`, `web`) | Not yet built. |
| Synthetic fixture corpus (`fixtures/`) | Not yet built. |

If you're reading this close to the submission date and some of these rows
haven't moved to "Live", that's the honest state of the repo, not a stale doc.

---

## One-command spin-up

This is a hard requirement we take seriously: a fresh GCP project should reach
public, working Cloud Run URLs with **no console clicks**.

```bash
# 1. Bootstrap the project: enables APIs, creates Firestore, GCS buckets,
#    Pub/Sub topics + subscriptions, and least-privilege service accounts.
#    Idempotent -- safe to run again if anything fails partway through.
PROJECT_ID=<your-project-id> BILLING_ACCOUNT=<your-billing-account-id> ./infra/setup.sh

# 2. Build and deploy every service to Cloud Run.
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

Full variable reference: [`.env.example`](.env.example). Deeper infra notes:
[`infra/README.md`](infra/README.md).

---

## Repository layout

```
everyfront/
├── infra/          # ATLAS — setup.sh, deploy.sh, service configs
├── services/
│   ├── intake/     # Gmail push webhook → Pub/Sub → GCS
│   ├── agent-core/ # the ADK agent hierarchy; live seed deployed
│   └── api/        # FastAPI REST for the dashboard
├── packages/
│   ├── rules/      # deterministic legal rules engine (deadlines, eligibility, FPL)
│   ├── datapipes/  # IRS/CMS/NCCI data pipelines
│   └── delivery/   # fax, certified mail, PDF fill, calendar, Drive
├── web/            # Next.js dashboard
├── fixtures/       # synthetic patient corpus, watermarked "SYNTHETIC — DEMO"
├── tests/          # unit + contract + e2e tests
└── docs/           # this document's diagram, video script, and docs/SPIKE.md
```

Every legal rule in `packages/rules` cites its regulation section in its
docstring, and the deadline math has no LLM calls anywhere in the package —
see [Working Agreement §2.1](BUILD_PLAYBOOK.md#2-non-negotiable-working-agreements)
if you want the full reasoning.

---

## Honest limitations

We would rather a judge learn these from us than discover them independently:

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
- **This is a 12-day build in active progress.** The status table above is the
  actual state of the repo, not a roadmap dressed up as a demo.

---

## License

[MIT](LICENSE).
