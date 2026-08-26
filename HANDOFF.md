# EVERY FRONT — Handoff

Paste this into a fresh session to pick the project up. Written 2026-08-26 by FORGE (CTO persona).

**Read `BUILD_PLAYBOOK.md` in full first.** It is the source of truth: personas (§4),
contracts (§3), working agreements (§2), rules of engagement (§0). This document
only covers what has actually happened and what is left.

---

## What this is

A medical bill lands in a Gmail inbox. An ADK multi-agent system identifies the
hospital from its own IRS filings, determines every legal front on which the bill
can be reduced or erased, computes every statutory deadline, fills the real forms,
and delivers them — autonomously, across a caseload.

Hackathon: **All Things Agentic (Google)**, track **The Taskmaster**.
Deadline **2026-08-31 5:00 PM PDT**, submit **Aug 30** with a buffer day.

The four legal fronts (§1.2): charity care (26 CFR 1.501(r)), No Surprises Act
patient-provider dispute resolution (45 CFR 149.620), debt validation
(12 CFR 1006.34 / 15 USC 1692g), and line-item billing audit.

---

## Live system

```
Dashboard    https://ef-web-756591166292.us-central1.run.app
API          https://ef-api-756591166292.us-central1.run.app
Agent core   https://ef-agent-core-756591166292.us-central1.run.app
Intake       https://ef-intake-756591166292.us-central1.run.app

GCP project  everyfront-hack-2026   (region us-central1)
Repo         https://github.com/jatishay07/everyfront   (PUBLIC)
```

**Vertex serves Gemini 3.x only from `location=global`.** `us-central1` returns 404
for `gemini-3.7-flash` and offers nothing newer than 2.5-flash — which is BELOW the
§1.3 "Gemini 3.5 or newer" pass/fail bar. A regional default would silently
disqualify the submission while appearing to work. Cloud Run stays regional; only
the model endpoint is global.

Budget guard: **$150** with alerts at 33 / 66 / 90 / 100%.

---

## State: 30 PRs merged, 44 commits, ~665 tests green

### Works, verified against the deployed system

| Capability | Evidence |
|---|---|
| Read a bill | Gemma 4 classifies, Gemini 3.7 extracts. All 7 agents log to the audit trail |
| Identify the hospital | Resolves by provider NAME against 204 real hospitals seeded from IRS Schedule H (bills rarely print an EIN) |
| Compute deadlines | Four legal clocks, each citing its regulation, with `.explain()` showing the arithmetic |
| Find overcharges | Real findings incl. the flagship: **CPT 86787 billed $140.00, hospital's own attested cash price $70.00** |
| Fill real forms | 5 live: `cms_ppdr`, `sutter_fap`, `advocate_fap`, `debt_validation_letter`, `records_request_letter` |
| Store the artifact | Filled PDFs in GCS. Verified: a 188,751-byte 4-page Sutter FAP with real AcroForm fields filled |
| Human-in-the-loop | `POST /cases/{id}/approve_filing` gates every filing. Returns in ~3.5s |
| Verifier | Correctly REFUSES to file: rejects a cat photo submitted as income proof, and a case with no income document |
| Event backbone | Pub/Sub push wired with OIDC. `filing.requested` → agent-core |
| Dashboard | Live on real data. Lighthouse 100 / 95 / 96 / 100 |
| Legal engine | **100% branch coverage** — 512 statements, 194 branches, zero LLM calls |

### Not working / not wired

- **Gmail intake** — code path complete, Cloud Scheduler renewal wired, but **no OAuth
  token has been minted**. Needs a human at a Google consent screen. See `infra/OAUTH.md`.
  The demo runs through `POST /demo/inject_bill`, which §6 sanctions as the primary path.
- **Calendar + Drive** — built and tested in `packages/delivery`, never called by the
  pipeline. RELAY left a precise HANDOFF with the exact call sites (PR #35).
- **Real fax/mail** — RELAY's interface is live; vendors are recording stubs. Phaxio and
  Lob offer free test keys but signup needs a human.
- **No authentication on any endpoint** — anyone with the URL reads every case and can
  approve filings. A deliberate, documented choice for a synthetic-data demo
  (`infra/README.md`), not an oversight. Would be indefensible with real data.
- **`case.analysis.complete` / `filing.completed`** remain PULL subscriptions with no
  handlers. Informational — Firestore state is written synchronously before publish.

### Closed since first writing (verified live)

- **The fabrication bug is fixed.** `ef-2026-0006` now returns `hospital_ein: None`,
  absent dates, all four fronts `applicable: false` with plain reasons, and **files
  nothing**. It declines instead of guessing.
- **The §2.1 duplication is gone.** `rules_bridge.py`'s `try/except ImportError` and its
  full reimplementation of `select_fronts` / `audit_line_items` /
  `check_denial_lawfulness` are deleted. One copy of the law, no drift.
- **The banner is honest**: `hospitals` 5 → 4, `charity_eligible` 6 → 5,
  `filings_sent` 11 → 7. The numbers got smaller because they got true.

---

## THE BUG PATTERN — read this before debugging anything

**Every serious defect in this project reported success while doing nothing.** None
crashed. All looked fine. Tests passed throughout. Each was found only by running the
deployed system and reading the actual output.

1. **The Filer filed placeholders for weeks.** SWARM guessed RELAY's import path
   (`delivery.fax`); RELAY shipped `delivery.vendors.fax`. A `try/except ImportError`
   swallowed the mismatch and every filing fell through to a simulated vendor id
   reporting `"status": "sent"`.
2. **All five Pub/Sub subscriptions were PULL with no subscriber.** `approve_filing`
   published into a queue nobody read; the front flipped to "filing" and nothing was
   produced. `setup.sh`'s own comment promised `deploy.sh` would convert them. It never did.
3. **`deploy.sh --source=services/<name>` uploaded only that directory.** Any service
   importing `packages/rules` built clean, passed every test, died at runtime.
4. **The agent-core container had `packages/delivery`'s code but not its dependencies.**
   Every real filing 500'd on `ModuleNotFoundError: pypdf`.
5. **The Reader fabricated data.** On an unreadable bill it invented EIN `00-0000000`
   and epoch dates `1970-01-01`, from which the Clock computed deadlines carrying real
   regulatory citations, and the Filer mailed a letter to "unknown hospital".
6. **Every filing was reported as a live send.** `send_filing()` never set the
   `simulated` flag the Filer reads.
7. **Every letter went out blank.** The templates read `patient["first_name"]` /
   `["address"]` — fields that don't exist in the contract.
8. **`GET /events` returned 500 on every request** for want of a Firestore
   collection-group index. That's the live activity feed, the demo's centrepiece.

**The rule that follows: never trust a green test suite or an agent's report about
runtime behaviour. Deploy, curl the real endpoint, read the actual bytes.** Four agent
claims were wrong today in both directions — two overclaimed, two underclaimed because
they measured a stale deployment.

**Always redeploy before believing any live measurement.** Two agents reported
contradictory audit results on the same day; both were honest, measuring different builds.

---

## Known open issues

1. ~~**THE ONE BLOCKER: a lost-update race on `fronts[]`.**~~ **FIXED and
   verified live, 2026-08-26** (FORGE, revision `ef-agent-core-00025-k54`).
   PROOF's diagnosis was right and incomplete -- the symptom had **three**
   independent causes, and only the first was the race:

   1. **Non-atomic `fronts[]` writes.** `upsert_front` and `run_filer` each
      did a read-modify-write of the whole array. Now a Firestore
      transaction, plus `set_front_status`, which re-reads the entry inside
      that transaction instead of writing the caller's snapshot. A
      transaction, not a per-case lock -- a lock would reintroduce exactly
      the approval timeout `ca9fd40` removed.
   2. **Re-analysis reopening filed fronts.** The Filer stores its generated
      PDF as a case document, which republishes `case.document.added`, which
      re-runs the hierarchy; `select_fronts` is pure and hands back every
      applicable front at "open", overwriting "filed". Live trace on
      ef-2026-0007 *after* the transaction was deployed: audit filed
      08:40:46, charity_care 08:40:51, re-analyses at 08:40:50 and
      08:40:52-54 reset both. `upsert_front_from_analysis` now preserves any
      status the filing lifecycle owns.
   3. **Writes resurrecting purged cases.** `.set(merge=True)` creates a
      missing document, so a late write after `demo_reset`'s
      rename-and-delete brought the old case back as a zombie in
      `GET /cases`. `update_case` and the fronts writer now no-op and return
      None when the case is gone.

   Evidence: the live corpus went from **5 inconsistent fronts** (across
   ef-2026-0001, -0003, -0007, each with a real "sent" filing behind an
   open/filing front) plus one stray case, to **0 and 0**. `demo-reset &&
   demo-run` then passed **twice consecutively**, exit 0, 120.3s and 129.4s
   of a 240s budget. PROOF's live banner reconciliation passes 3/3.

   Each cause has a regression test verified to FAIL against the pre-fix code.

2. ~~**`fixtures/requirements.txt` isn't installed in CI**~~ -- fixed; it is
   in the install glob now. CI also now runs `services/agent-core/tests` and
   `services/api/tests`, which root `testpaths` had been excluding entirely --
   every regression test for a defect that only ever appeared in a deployed
   service was sitting there unrun. Three separate pytest invocations, because
   the two `test_store.py` basenames collide under pytest's default import mode.
3. **Demo corpus is unstable** when multiple agents test concurrently against the shared
   live Firestore. Do a final reseed before recording. Keep exactly 8 cases,
   `ef-2026-0001`..`0008`.
4. **Destructive Firestore deletes are blocked for subagents** but permitted for a main
   session. If an agent says it cannot purge, that is real — do it yourself.

---

## How to work on this

```bash
# tests / lint (CI runs exactly these)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest -q -m "not e2e"

# infrastructure (idempotent, safe to re-run — that IS the recovery path)
PROJECT_ID=everyfront-hack-2026 BILLING_ACCOUNT=012403-BC81D0-EC308C ./infra/setup.sh
PROJECT_ID=everyfront-hack-2026 ./infra/deploy.sh agent-core   # or api / web / intake / all

# drive the demo
curl -X POST $API/demo/inject_bill -H 'Content-Type: application/json' \
     -d '{"fixture_name":"case_01_uninsured_gfe_ca"}'
curl -X POST $API/cases/{id}/approve_filing -H 'Content-Type: application/json' \
     -d '{"front":"charity_care"}'
```

**`gh` gotcha:** a stale `GITHUB_TOKEN` in `~/.zshrc` shadows the working keyring
credential. Prefix every command: `GITHUB_TOKEN= gh pr create ...`

**Parallel agents work well here** because §1.5 gives every persona disjoint directory
ownership, so they cannot collide on files. Give each an isolated git worktree. What
they DO collide on is the shared live GCP project and the contracts in §3 — when a
contract changes mid-flight, message the running agents.

**Tell agents to verify rather than trust.** The two most valuable findings of the day
came from agents that ignored their brief and checked: PROOF found the fabrication bug
that another agent had dismissed as "not a bug", and MEGAPHONE traced a root cause while
fact-checking a README claim.

---

## What's left before submission

**Blocking — all clear as of 2026-08-26**
- [x] Remove the duplicated front-selection logic from `services/agent-core` (PR #36)
- [x] Confirm `charity_care` refuses on an unresolved hospital, live
- [x] Confirm the `hospitals` stat drops to 4
- [x] Final reseed to exactly 8 cases, banner reconciles (PROOF's live test, 3/3)
- [x] The `fronts[]` race — three causes, all fixed and deployed (see above)

**Demo**
- [x] `make demo-reset && make demo-run` passing twice consecutively — exit 0
      both times, 120.3s and 129.4s of a 240s budget, "all approved fronts filed"
- [ ] Three full rehearsals (§5)
- [ ] Record the ~4-minute video — must show live execution AND visible proof of Google
      Cloud deployment (a §1.3 hard requirement). `docs/VIDEO_SCRIPT.md` has the beats.
      The Verifier rejecting a cat photo submitted as income proof is a genuinely
      memorable 15 seconds.

**Submission (§1.3 — Stage One is pass/fail)**
- [ ] Devpost by Aug 30. Draft in `docs/DEVPOST_SUBMISSION.md`
- [ ] Verify the video is public, not "Made for Kids", under length, in English
- [ ] Architecture diagram is explicitly named in the rubric — `docs/architecture.svg`
- [ ] Bonus 0.6: blog 0.2, social with #AllThingsAgenticHackathon 0.2, Gemma 0.2
      (Gemma does real first-pass classification — name it explicitly)

**Optional**
- [ ] Mint the Gmail OAuth token (`infra/OAUTH.md`) so a bill can genuinely arrive by email
- [ ] Wire Calendar + Drive (RELAY's HANDOFF in PR #35)

---

## The honest assessment

The analysis half is genuinely strong: the legal engine is deterministic with 100%
branch coverage, every rule cites a verified primary source, and the hospital data is
real — reconstructed from IRS Schedule H filings and CMS price-transparency files.

The action half — fill the real form, file it, show the artifact — works and has been
demonstrated end to end, but it took finding eight silent failures to get there and one
(#1 above) is still open.

**This is a strong hackathon build with a real, demonstrable core and honest failure
modes. It is not a system to route a real patient's bill through**, and the README says
so. That honesty is a scoring asset (§4 persona 8), not a liability.
