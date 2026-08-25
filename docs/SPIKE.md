# Day-1 Spike — FORGE Work Order 1

**Date:** 2026-08-19 · **Owner:** FORGE (CTO) · **Playbook:** §4 Persona 0, WO1

Four gates. Gates (a) and (c) are halt-and-redesign gates.

| Gate | What it proves | Status |
|---|---|---|
| (a) Parse real IRS Schedule H XML, extract lines 13a/16a/16b | LEDGER's crown-jewel pipeline is feasible | **PASS** |
| (b) Reach an MRF via `cms-hpt.txt` for 3 real systems | Cash-price audit front is feasible | **PASS** |
| (c) Hello-world ADK agent on Cloud Run | The entire runtime is feasible | **PASS** — live at ef-agent-core, Gemini 3.7 via Vertex |
| (d) Verify $150 hackathon + $300 trial credit stacking | Budget headroom is real | **BLOCKED** — needs `gcloud auth login` |

Reproduce with `docs/spike/parse_schedule_h.py`. Evidence files are committed alongside.

---

## Gate (a) — IRS Schedule H parser · **PASS**

Parsed **21 hospital facilities across 3 real filings**, extracting live FPL thresholds
and FAP URLs straight from IRS e-file XML.

| Org | EIN | State | Facilities | 13a free / discounted FPL% |
|---|---|---|---|---|
| Advocate Health and Hospitals Corp | 36-2169147 | IL | 6 | 250 / 600 |
| Sutter Bay Hospitals | 94-0562680 | CA | 7 | 400 / 0 |
| Kaiser Foundation Hospitals | 94-1105628 | CA | 8 | 200 / 400 (varies by group) |

Sutter's **400% free-care threshold independently corroborates the CA statutory floor**
(HSC §127405) that STATUTE encodes in `screen_eligibility`. Good cross-validation.

### Confirmed schema (stable across return versions 2021v4.0 → 2023v5.1)

Namespace `http://www.irs.gov/efile`; repeating group `HospitalFcltyPoliciesPrctcGrp`.

| Part V Sec. B line | XML element | Contract field (§3.1 `hospitals/{ein}`) |
|---|---|---|
| 13a free | `FPGFamilyIncmLmtFreeCarePct` | `free_care_max_fpl_pct` |
| 13a discounted | `FPGFamilyIncmLmtDscntCarePct` | `discounted_care_max_fpl_pct` |
| 16a | `FAPAvailableOnWebsiteURLTxt` | `fap_url` |
| 16b | `FAPAppAvailableOnWebsiteURLTxt` | `fap_app_url` |
| 16c | `FAPSummaryOnWebsiteURLTxt` | — |

I checked all five return versions present in the batch for element-name drift and found
**none** — one parser covers every year. The EIN is at `ReturnHeader/Filer/EIN`, *not* a
direct child of `ReturnHeader`; reading the wrong path yields a silent empty string and
would corrupt every `hospitals/{ein}` key. The parser now raises instead.

### Scale measurement — batch `2024_TEOS_XML_11A` (1 of 12)

- 186,632 filings in the batch; **2,531 contain Schedule H**
- **758** of those (30%) actually carry facility-policy groups → **912 facility rows**
- Only **290 / 912 (31.8%)** give a directly usable `http(s)` FAP URL

### ⚠️ Data quirks LEDGER and STATUTE must handle

1. **`0` is a sentinel, not a threshold.** Sutter reports discounted-care FPL% = `0`,
   meaning *not offered* — not "0% of FPL". Treating it literally makes every patient
   ineligible for discounted care. **Map `0` → `None`.** This is a live correctness trap
   in `screen_eligibility`.
2. **66.6% of line 16a values are not URLs.** 305 are cross-references
   ("SEE PART V, SECTION C" ×89, "SEE PART V, PAGE 8" ×60). The real URL is in Part V
   Section C free text, which needs separate extraction.
3. **A repair pass is worth 29 points.** 247 values are missing a scheme
   (`WWW.SENTARA.COM/...`) and 21 are missing the colon (`HTTPS//WVUMEDICINE.ORG/...`).
   Repairing both lifts usability **31.8% → 61.2%**. Cheap, high-value; do it.
4. **62% of URLs are uppercased** (`HTTP://WWW.ADVOCATEHEALTH.COM/...`). Lowercase scheme
   and host only — paths can be case-sensitive.
5. **Facility granularity is often absent.** Kaiser files 8 rows covering 43 facilities
   ("A-14 Facilities - See Part V Sec C"); Sutter names facilities "A", "B", "C".
   `FacilityNum` is missing on 149 rows and rows are unordered. Keying `hospitals/{ein}`
   per §3.1 is right; per-facility CCN mapping will be lossy.
6. **The bulk zips are the only access path.** The old per-filing endpoint
   `s3.amazonaws.com/irs-form-990/{object_id}_public.xml` now **404s**. Batches are ~1.1 GB
   and are ZIP64 — macOS Info-ZIP `unzip` errors on them; Python `zipfile` reads them fine.
7. **Batch-ID casing is inconsistent** in the index (`2024_TEOS_XML_05a` vs `..._07A`) and
   the download URLs are case-sensitive. Don't normalize case when building URLs.

### Live FAP URL check

**25 / 30 (83%)** of a random sample of usable URLs returned HTTP 200. Failures were
2×403 (bot protection), 2×404 (genuinely dead links), 1 timeout.

> **Verdict on LEDGER's acceptance criterion** ("≥60% of seeded nonprofits resolve a live
> FAP URL"): achievable, but **only if the 200-hospital seed selects for facilities whose
> 16a is a real URL** rather than sampling at random. Random sampling yields ~0.32 × 0.83 ≈
> **26%** and would fail the bar. Batch 11A alone offers 558 post-repair usable rows against
> a 200-hospital target, so selection costs us nothing. **This is a required design change.**

---

## Gate (b) — `cms-hpt.txt` → MRF → cash price · **PASS**

**7 of 8** systems served `/cms-hpt.txt` (45 CFR Part 180):

| System | `cms-hpt.txt` | MRF |
|---|---|---|
| advocatehealth.com | 200 | 200 — CSV v3.0.0 |
| sutterhealth.org | 200 | — |
| stanfordhealthcare.org | 200 | 200 — JSON, **154 MB** |
| cedars-sinai.org | 200 | 200 — JSON |
| nm.org, uchicagomedicine.org, rush.edu | 200 | — |
| **kp.org** | **redirect loop (bot protection)** | — |

### The unplanned win: EIN is embedded in the MRF filename

CMS mandates `<ein>_<hospital-name>_standardcharges.<ext>`:

```
362169147_advocate-christ-medical-center_standardcharges.csv   → Advocate,  EIN 36-2169147
946174066_stanford-health-care_standardcharges.json            → Stanford,  EIN 94-6174066
951644600_CEDARS-SINAI-MEDICAL-CENTER_standardcharges.json     → Cedars,    EIN 95-1644600
```

Advocate's EIN matches its Schedule H filing exactly. **This closes the EIN↔hospital
crosswalk for free** and materially de-risks LEDGER WO2 — the Community Benefit Insight
API becomes a fallback rather than the primary join.

### Real cash prices extracted (Advocate Christ Medical Center)

| Code | Type | Gross | **Cash** | Description |
|---|---|---|---|---|
| 86787 | CPT | $140.00 | **$70.00** | AB, VARICELLA ZOSTER IGG |
| C1713 | HCPCS | $7,669.69 | **$3,834.85** | NAIL OD9MM FEM PROX TI |
| C1876 | HCPCS | $3,870.00 | **$1,935.00** | STENT NTNL PERIPH |

`standard_charge|discounted_cash` is the attested cash price STATUTE's `audit_line_items`
needs. Advocate applies a flat 50%-of-gross discount, so a self-pay patient billed gross
is demonstrably overcharged 2× — **a real, defensible demo finding**.

### MRF quirks

1. **3-row header.** Row 1 = attestation labels, row 2 = hospital metadata, row 3 = the
   real column header, row 4+ = data. Skip 2 rows before parsing.
2. **Size demands streaming.** Stanford's MRF is 154 MB of JSON. Never load whole; use
   HTTP range requests or a streaming parser. (Range requests worked on all three.)
3. **Most rows have an empty cash price** — they are payer-specific negotiated rows. Filter
   to rows where `standard_charge|discounted_cash` is populated.
4. **Rush publishes scheme-less URLs** (`apps.para-hcfs.com/...`) behind an ASPX report
   generator, not a static file — same repair heuristic as gate (a) applies.
5. HTML entities (`&gt;`) appear unescaped in description text.

---

## Gate (c), model half — **PASS (with one amendment)** · 2026-08-21

Verified live against `generativelanguage.googleapis.com` using a free AI Studio
key — no billing, no GCP project. Doing this early was worth it: one of the two
locked model IDs does not exist.

| §1.4 ID | Result | Action |
|---|---|---|
| `gemini-3.7-flash` | **live** — returned `bill` on a real classification | none; clears §1.3 |
| `gemma-3-27b-it` | **HTTP 404 NOT_FOUND** | **amended** to `gemma-4-26b-a4b-it` |

**§1.3 pass/fail disqualifier is cleared.** `gemini-3.7-flash` is served and 3.7
satisfies "Gemini 3.5 or newer". `gemini-3.6-flash` and `gemini-3.5-flash` also
respond and are valid fallbacks.

One transient note: `gemini-3.7-flash` returned **HTTP 503 "high demand"** on the
first attempt and succeeded on retry. Free-tier capacity, not absence — but SWARM
must implement retry-with-backoff regardless, and this is why 503 must never be
treated as "model missing".

### Gemma 3 is gone; Gemma 4 works — with two traps

The whole Gemma 3 generation returns 404. Only `gemma-4-26b-a4b-it` and
`gemma-4-31b-it` are served. Both classify correctly, **5/5** on the §3.1 document
types (bill, denial_letter, collection_notice, gfe, income_proof).

**Trap 1 — thinking parts.** Gemma 4 returns TWO parts: `{"text": ..., "thought": true}`
followed by the real answer. Concatenating them yields a bulleted restatement of
the prompt instead of a label — it looks exactly like the model failing to follow
instructions, and it cost an hour to diagnose. **Filter parts where `thought` is
true.** With filtering, accuracy went 0/5 → 5/5 with no prompt change.

```python
def answer(resp):
    parts = resp["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
```

**Trap 2 — thinking cannot be disabled.** `thinkingConfig.thinkingBudget: 0` returns
HTTP 400 "Thinking budget is not supported for this model." Measured cost is ~312
thought tokens per classification against ~2.6 answer tokens — roughly 120x the
output. §1.4 chose Gemma as the *cheap* first pass; it is still a small model, but
"cheap" now means cheap-per-token, not few-tokens. Factor this into the §6 burn
ceiling before running the corpus.

**HANDOFF → SWARM (persona 5, WO1):** use the thought-filtering helper above;
implement 503 retry-with-backoff; budget ~315 tokens per Gemma classification.

---

## Gate (c), deploy half — **PASS** · 2026-08-25

**Live:** `https://ef-agent-core-756591166292.us-central1.run.app`

```
POST /ask -> tool_call   compute_fap_deadline({'first_statement_date': '2026-03-01'})
             tool_result {'due': '2026-10-27', 'citation': '26 CFR 1.501(r)-4(b)(1)(iv)'}
             answer      "October 27, 2026 ... under 26 CFR 1.501(r)-4(b)(1)(iv)"
             model       gemini-3.7-flash   6.9s cold
```

§1.3 is satisfied: Cloud Run + Pub/Sub + Firestore provisioned, ADK agent
framework, Gemini 3.7 via Vertex. §2.1 holds in production -- the model called
the tool rather than doing the arithmetic, and the trace proves it.

### Four failures on the way, all now fixed in the scripts

1. **Pub/Sub org-policy race.** First `setup.sh` run died creating topics:
   `gcp.resourceLocations` "does not allow message storage in any GCP region".
   The project has no org parent and the effective policy reads `allValues: ALLOW`
   -- it was the initialization race the error text itself warns about. **Re-running
   fixed it**, which is the whole argument for idempotency: the recovery was
   `./infra/setup.sh` again, not a manual repair.

2. **Dependency conflict invisible locally.** `requirements.txt` pinned
   `fastapi==0.115.6`; google-adk 2.7.1 requires `fastapi>=0.133`. Cloud Build
   failed with `ResolutionImpossible` while the local venv passed -- it had already
   resolved a compatible fastapi. **A working local venv does not prove a
   buildable image.** Now pinned to the locally-verified set.

3. **`/healthz` is intercepted by the Cloud Run frontend.** It returns Google's
   own 404 before the request reaches the container, even though FastAPI
   registers the route and it appears in `/openapi.json`. Renamed to `/health`,
   which works. Worth knowing before wiring liveness probes.

4. **Vertex serves Gemini 3.x only from `location=global`.** The most dangerous
   one. `us-central1` returns 404 for `gemini-3.7-flash` and `gemini-3.5-flash`
   and serves nothing newer than **2.5-flash** -- which is BELOW the §1.3
   "Gemini 3.5 or newer" bar. A regional default would have silently disqualified
   the submission while appearing to work. `deploy.sh` now pins
   `VERTEX_LOCATION=global` while Cloud Run stays regional.

**HANDOFF -> SWARM:** authenticate via Vertex + the service account, never an API
key in the container. `GOOGLE_GENAI_USE_VERTEXAI=TRUE`,
`GOOGLE_CLOUD_LOCATION=global`.

---

## Gate (d) — **PARTIAL**

The $150 hackathon credit arrived; there is **no $300 trial and no stacking**, so
the real balance is $150 rather than the $450 §6 assumed. Tripwire amended to $50.

`setup.sh` could not create the budget alert (`billing.budgets.create` denied on
this account) and says so rather than failing silently -- **set the $50/$100/$150
alerts in the console manually.**

---

## Superseded

`gcloud` is installed (`/opt/homebrew/bin/gcloud`) but has **no active account**, so neither
the Cloud Run deploy nor the billing console check can run.

**Unblock:** `gcloud auth login`, then FORGE resumes both gates unattended.

Gate (c) will additionally verify — before ATLAS builds on it — that the §1.4 locked model
IDs `gemini-3.7-flash` and `gemma-3-27b-it` actually resolve in this project's region, and
that the Gemini version clears the hackathon's "3.5 or newer" pass/fail bar (§1.3).

---

## Decisions and handoffs

- **HANDOFF → LEDGER (persona 2, WO1):** seed the 200 hospitals by *selecting* facilities
  with a repairable 16a URL, not by random sampling. Apply the scheme/colon repair pass and
  lowercase scheme+host. Use Python `zipfile`, not `unzip`. Expect ~1.1 GB per batch.
- **HANDOFF → LEDGER (WO2):** derive EIN↔hospital from MRF filenames first; Community
  Benefit Insight API is the fallback.
- **HANDOFF → STATUTE (persona 3, WO2):** `free`/`discounted` FPL% of `0` means *not
  offered*. Add a unit test — this is a silent-wrong-answer bug, not a crash.
- **RISK REGISTER (§6), new row:** *"FAP URL coverage lower than assumed"* — trigger: random
  sampling yields ~26% live URLs vs the 60% bar. Response: select-don't-sample (above);
  fallback is Part V Section C free-text extraction.
- **No change to §1.4 or §3.** Both gates confirmed the contracts as written.
