# Devpost submission draft — Every Front

**Owner:** MEGAPHONE (persona 8) · **Submit by:** Aug 30 (24h buffer before the
Aug 31, 5:00 PM PDT deadline, per BUILD_PLAYBOOK.md §4 persona 8 WO6)

This is a paste-ready draft for the Devpost project form. Every factual claim
below is sourced to something committed in this repo — `README.md`,
`docs/SPIKE.md`, or a live curl against the deployed services on 2026-08-25 —
so nothing here needs to be re-verified before submitting, but **do re-check
the live URLs and the exact filings/savings numbers on submission day**, since
other agents' work (SWARM's latency fix, the audit line-item wiring) may
change them between now and Aug 30.

**Before submitting, confirm:** video is public (not unlisted/private), not
marked "Made for Kids," under the length limit, in English, and that the
Devpost form's project links resolve (repo, live URLs, video).

---

## Project name

Every Front

## Tagline

An agentic system that finds every legal front to reduce or erase a medical
bill — and files the paperwork itself.

## Track

The Taskmaster

## Links

- **GitHub (public repo):** https://github.com/jatishay07/everyfront
- **Live API:** https://ef-api-756591166292.us-central1.run.app
- **Live agent-core:** https://ef-agent-core-756591166292.us-central1.run.app
- **Demo video:** _[insert link once recorded and uploaded, per §5: record
  Aug 29, submit Aug 30]_

## Built with

Google ADK (Python) · Gemini 3.7 Flash (Vertex AI) · Gemma 4
(`gemma-4-26b-a4b-it`) · Google Cloud Run · Google Cloud Pub/Sub · Google
Cloud Firestore · Google Cloud Storage · Gmail API · Google Calendar API ·
Google Drive API · Next.js 14 · Tailwind CSS · Python 3.12 · FastAPI ·
pypdf · reportlab · Phaxio · Lob

---

## Inspiration

76% of patients who qualify for free hospital charity care never apply for
it, because nobody tells them it exists. Less than 1% of medical billing
denials are ever appealed, and about a third of the people who do appeal win.
Roughly $14B a year in charity care that hospitals are legally required to
offer goes unclaimed. These aren't numbers we derived ourselves — they're
widely cited in patient-advocacy and health-policy reporting (Dollar For,
KFF, Commonwealth Fund) and we say so plainly in our own README rather than
present them as something we proved.

What we *did* verify ourselves, against primary sources: a hospital's
charity-care policy is sitting in public IRS Form 990 Schedule H filings, and
its actual cash price for a given procedure is sitting in a CMS-mandated
machine-readable price file. Nobody has to guess what a patient is owed —
the hospital's own regulatory filings already say so. The problem isn't a
missing database. It's that nobody reads 900,000 tax filings and cross-checks
them against a bill on a patient's kitchen table. An agent can.

## What it does

A medical bill lands in an inbox (or is injected via
`POST /demo/inject_bill` for the demo). Every Front:

1. **Reads** the document — Gemma 4 classifies it first (bill, denial
   letter, collection notice, GFE, income proof), then Gemini 3.7 Flash
   extracts structured fields at temperature 0 against a JSON schema.
2. **Looks up** the hospital by EIN against a Firestore record seeded
   straight from that hospital's IRS Schedule H filing — its charity-care
   income thresholds, its FAP application URL — and is honest when the
   hospital is for-profit and owes no such duty at all.
3. **Computes every deadline and eligibility determination in pure,
   unit-tested Python** — zero LLM calls, every result citing its federal
   regulation or state statute. The LLM narrates what the code decided; it
   never does the arithmetic itself.
4. **Selects and sequences** the applicable legal fronts — charity care,
   Patient-Provider Dispute Resolution, debt validation, billing audit —
   getting the *ordering* right (debt validation first when a case is in
   collections, because it's the one that freezes everything else).
5. **Fills the real forms** — the actual CMS PPDR dispute form and two real
   hospitals' own FAP applications — and waits for a human to click
   **Approve** before anything is sent.
6. **Files it** by fax or certified mail behind one swappable vendor
   interface, records delivery proof, and puts every deadline on a shared
   Google Calendar with the citation in the event description.

Every step is logged to an event stream a human can watch in real time in
the dashboard — this is the audit trail the whole design is built around.

## How we built it

Six ADK agents (Reader, Lookup, Clock, Auditor, Strategist, Verifier, Filer —
Clock and Auditor share one thin LLM wrapper) run in a single agent
hierarchy on Cloud Run, triggered by Pub/Sub. The legal logic — deadline math,
FPL eligibility screening, front selection, NCCI billing-audit checks, denial
triage — lives entirely in `packages/rules`, a dependency-free Python package
with 182 tests at 100% branch coverage and zero LLM calls; every function
cites its regulation in its docstring. `packages/datapipes` parses IRS
Schedule H bulk XML and CMS price-transparency files directly, seeding 200
real hospitals to Firestore with a 100% live FAP-URL rate. A FastAPI service
exposes the case/event/filing model to a Next.js dashboard, and
`packages/delivery` fills five real PDF forms and sends them through Phaxio
and Lob behind one interface with a hard destination allowlist enforced in
code. `infra/setup.sh` and `infra/deploy.sh` take a bare GCP project to four
working Cloud Run services with no console clicks.

## Challenges we ran into

- **A locked model ID that doesn't exist.** `gemma-3-27b-it` returns HTTP 404
  — the whole Gemma 3 generation is retired. We caught this on Day 1 by
  hitting the API directly before building anything on top of it, and moved
  to `gemma-4-26b-a4b-it`.
- **Vertex serves Gemini 3.x only from `location=global`.** `us-central1`
  silently falls back to a model below the hackathon's "3.5 or newer" bar —
  the kind of failure that looks like success until someone checks the model
  string.
- **A silent-wrong-answer bug, not a crash.** Reader's extractor returns `0`
  and `""` for fields it didn't find, not `None`. Filtering on `is not None`
  let those sentinels overwrite real values, so a real bill amount silently
  became `$0` downstream — every number in the case, not just one, and
  nothing errored. It's now filtered on type and truthiness, with the bug's
  own trace kept in the code as a comment for whoever touches that function
  next.
- **We're shipping the same honesty about our own gaps that we ask hospitals
  to have.** The dollar figure for billing-audit findings currently reads
  $0 in a live run, because the extraction step doesn't yet hand the audit
  engine structured line items. We say so in the README and in the video
  instead of picking a case that hides it.

## Accomplishments that we're proud of

- The legal engine has **zero LLM calls** and **100% branch coverage** —
  every deadline and eligibility result is arithmetic a court could check,
  not a language model's guess.
- **200 real hospitals**, seeded from their own tax filings, not a
  third-party database — with a **100% live FAP-URL rate**.
- A real, defensible overcharge finding: Advocate Christ Medical Center's own
  published price file shows a **2× markup** for a self-pay patient billed
  the gross rate instead of the attested cash price.
- The whole pipeline runs end to end against the live Cloud Run services —
  we curled it ourselves while writing this submission and got a real case
  back with 30 logged events and 4 evaluated legal fronts, citations
  included.

## What we learned

That the hardest part of this problem was never "can an LLM read a medical
bill" — it's "can you build a system honest enough to say when a hospital
owes nothing, when a document doesn't classify, or when your own audit
number isn't wired up yet." The trustworthy parts of this system are the
parts that refuse to guess.

## What's next

Wiring the audit engine's structured line-item extraction so the
billing-audit dollar figure reflects real findings; adding name-based
hospital resolution as a fallback to EIN lookup; moving from vendor test
credentials to live Phaxio/Lob accounts; and expanding the hospital seed
past 200 using the same pipeline.
