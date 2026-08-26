# Devpost submission draft — Every Front

**Owner:** MEGAPHONE (persona 8) · **Submit by:** Aug 30 (24h buffer before the
Aug 31, 5:00 PM PDT deadline, per BUILD_PLAYBOOK.md §4 persona 8 WO6)

This is a paste-ready draft for the Devpost project form. Every factual claim
below is sourced to something committed in this repo (`README.md`,
`docs/SPIKE.md`) or to a live curl / live Firestore query run on 2026-08-25
while writing this pass — including a couple of corrections to claims an
earlier pass made that didn't hold up under re-verification. **Do re-check the
live URLs and the exact filings/savings numbers on submission day** — the
system keeps changing, and this draft says exactly which numbers are still
open questions (see `README.md`'s "Honest limitations" for the live list).

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
- **Live dashboard:** https://ef-web-756591166292.us-central1.run.app
- **Live API:** https://ef-api-756591166292.us-central1.run.app
- **Live agent-core:** https://ef-agent-core-756591166292.us-central1.run.app
- **Demo video:** _[insert link once recorded and uploaded, per §5: record
  Aug 29, submit Aug 30]_

## Built with

Google ADK (Python) · Gemini 3.7 Flash (Vertex AI) · Gemma 4
(`gemma-4-26b-a4b-it`) · Google Cloud Run · Google Cloud Pub/Sub · Google
Cloud Firestore · Google Cloud Storage · Gmail API · Next.js 14 · Tailwind
CSS · Python 3.12 · FastAPI · pypdf · reportlab · Phaxio (test mode) · Lob
(test mode)

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
missing database. It's that nobody reads hundreds of thousands of tax
filings and cross-checks them against a bill on a patient's kitchen table.
An agent can.

## What it does

A medical bill lands in an inbox (or is injected via
`POST /demo/inject_bill` for the demo). Every Front:

1. **Reads** the document — Gemma 4 classifies it first (bill, denial
   letter, collection notice, GFE, income proof), then Gemini 3.7 Flash
   extracts structured fields at temperature 0 against a JSON schema.
2. **Looks up** the hospital — by EIN, or by provider-name match when the
   bill carries no tax ID — against a Firestore record seeded straight from
   that hospital's IRS Schedule H filing, and is honest when the hospital is
   for-profit and owes no charity-care duty at all.
3. **Computes every deadline and eligibility determination in pure,
   unit-tested Python** — zero LLM calls, every result citing its federal
   regulation or state statute. The LLM narrates what the code decided; it
   never does the arithmetic itself.
4. **Selects and sequences** the applicable legal fronts — charity care,
   Patient-Provider Dispute Resolution, debt validation, billing audit —
   getting the *ordering* right (debt validation first when a case is in
   collections, because it's the one that freezes everything else).
5. **Verifies before it files** — cross-checks an uploaded income document
   against the case, and genuinely refuses to file when it can't. We
   reproduced this live while writing this draft: a photo of a cat uploaded
   as "proof of income" gets a real HTTP 409 with a plain-English reason, and
   so does a charity-care filing with no income document on file at all.
6. **Fills the real forms** — the actual CMS PPDR dispute form and real
   hospital FAP applications (we regenerated all three ourselves: 321,809
   bytes, 188,739 bytes, 387,855 bytes of real, filled `%PDF-` output) — and
   waits for a human to click **Approve** before anything is sent.
7. **Files it** by fax or certified mail behind one swappable vendor
   interface and records delivery proof (test-mode vendor credentials only —
   see Challenges).

Every step is logged to an event stream a human can watch in real time in
the dashboard — this is the audit trail the whole design is built around.

## How we built it

Six ADK agents (Reader, Lookup, Clock, Auditor, Strategist, Verifier, Filer —
Clock and Auditor share one thin LLM wrapper) run in a single agent
hierarchy on Cloud Run. The legal logic — deadline math, FPL eligibility
screening, front selection, NCCI billing-audit checks, denial triage — lives
entirely in `packages/rules`, a dependency-free Python package we
re-measured ourselves at **100% statement and branch coverage** (489
statements, 180 branches, zero misses) with zero LLM calls anywhere in it;
every function cites its regulation in its docstring. It's exercised by 364
of the **665 tests passing** across the whole repo today. `packages/datapipes`
parses IRS Schedule H bulk XML and CMS price-transparency files directly,
seeding **204 real hospitals** to Firestore — we queried the live database
directly while writing this and counted them ourselves — with **201/204
(98.5%) carrying a live FAP URL**. A FastAPI service exposes the case/event/
filing model to a Next.js dashboard (confirmed live and pointed at the real
API, not mock data), and `packages/delivery` fills real PDF forms and sends
them through Phaxio and Lob (test mode) behind one interface with a hard
destination allowlist enforced in code. `infra/setup.sh` and
`infra/deploy.sh` take a bare GCP project to four working Cloud Run services
with no console clicks.

## Challenges we ran into

- **A locked model ID that doesn't exist.** `gemma-3-27b-it` returns HTTP 404
  — the whole Gemma 3 generation is retired. We caught this on Day 1 by
  hitting the API directly before building anything on top of it, and moved
  to `gemma-4-26b-a4b-it`.
- **Vertex serves Gemini 3.x only from `location=global`.** `us-central1`
  silently falls back to a model below the hackathon's "3.5 or newer" bar —
  the kind of failure that looks like success until someone checks the model
  string.
- **A try/except hid a real integration for weeks.** The Filer used to
  render a five-line text placeholder because its bridge into the delivery
  package guessed the wrong import path and swallowed the `ImportError`
  instead of failing loudly. The real form-filling code was fully built and
  tested the whole time; nothing ever called it. We fixed the bridge to fail
  loudly instead of degrading silently, and independently regenerated all
  three real filled forms ourselves to prove it.
- **We briefly got the Verifier's own honesty backwards.** An earlier
  internal note described one of its blocks as a false positive. It wasn't —
  re-tested live, both of its blocks (the cat-photo document and the
  missing-income-document case) are the system correctly refusing to file
  incomplete paperwork. We corrected the record rather than let a
  mischaracterization of our own best feature stand.
- **The billing-audit dollar figure is a gap we found ourselves, mid-pass,
  and it's still open.** Driven directly with the real bundled NCCI table
  and a real live-fetched hospital cash price, the audit engine computes an
  exact, defensible $1,217.50 across 6 findings on one of our fixture bills.
  But re-running the same fixture live against the deployed system, twice,
  returned only $220.00 (one finding) both times — the hospital's cash-price
  data is visibly present on the case via the API but isn't reliably
  reaching the auditor inside the pipeline run itself. We're naming this
  precisely, with the repro, rather than picking a case that hides it or
  quietly rounding up.
- **Infrastructure and the repo drifted.** Live `gcloud pubsub subscriptions`
  checks show 3 of 5 Pub/Sub subscriptions already converted to push
  delivery — but the code that does this exists only in an open, unmerged
  pull request. Someone applied it to the running project without merging.
  It's a reminder that "verify the repo" and "verify what's actually
  running" are two different checks, and we do both.

## Accomplishments that we're proud of

- The legal engine has **zero LLM calls** and **100% branch coverage**,
  re-measured directly against the live test suite while writing this —
  every deadline and eligibility result is arithmetic a court could check,
  not a language model's guess.
- **204 real hospitals**, seeded from their own tax filings, not a
  third-party database — counted directly against the live Firestore
  project, not taken on faith.
- A real, defensible overcharge finding, re-fetched live from the hospital's
  own published file while writing this submission: Advocate Christ Medical
  Center bills **$140.00** and accepts **$70.00** cash for CPT 86787 — a flat
  50%-of-gross discount, and a self-pay patient billed gross is being
  overcharged 2× on the hospital's own numbers.
- **The Verifier genuinely refuses to file bad paperwork**, and we proved it
  twice, live, against the deployed API, while writing this draft — not just
  in a unit test.
- The whole pipeline runs end to end against the live Cloud Run services —
  we called it three separate times while writing this submission and got
  real cases back with real citations, a real filled PDF, and a real
  (test-mode) delivery record.
- **A clean charity-care filing, real PDF, end to end, live** — something no
  earlier pass through this project had actually shown working. We approved
  `charity_care` on a live case and got a real 387,861-byte Advocate FAP
  application back, uploaded to a real GCS object, filed and sent
  (test-mode).

## What we learned

That the hardest part of this problem was never "can an LLM read a medical
bill" — it's "can you build a system honest enough to say when a hospital
owes nothing, when a document doesn't classify, when your own audit number
isn't reliably wired end to end yet, or when your own prior claim about your
own system was wrong." The trustworthy parts of this system are the parts
that refuse to guess — including, this pass, about ourselves.

## What's next

In priority order: get the live pipeline to actually surface the audit
engine's cash-price and NCCI findings it's provably capable of computing;
merge and redeploy the open Pub/Sub push-wiring PR so the repo matches what's
running; wire `packages/delivery`'s already-built Calendar and Drive sync
into the live pipeline (currently built, tested, and never called); mint real
Gmail OAuth credentials so intake can run against a real inbox instead of
`/demo/inject_bill`; move from vendor test credentials to live Phaxio/Lob
accounts; add authentication to every endpoint before this touches anything
but synthetic data; and reseed a clean, human-readable demo corpus before the
next recording session.
