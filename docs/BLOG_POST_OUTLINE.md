# Blog post outline — "Rebuilding a closed national hospital-charity database from public IRS filings"

**Owner:** MEGAPHONE (persona 8), per BUILD_PLAYBOOK.md §4 persona 8 WO4
(bonus: published content, 0.2 of the §1.3 bonus scoring).
**This is an outline, not a draft** — each section lists what it needs to say
and which real artifact in the repo backs it, so whoever writes the full post
(could still be MEGAPHONE) isn't inventing numbers.

**Where the numbers come from:** `docs/SPIKE.md` (the Day-1 spike, all figures
pre-scale) and `packages/datapipes` + its commit message (the production run:
200 hospitals seeded, 100% live FAP URL rate, 2,881 NCCI pairs, 15,112 MUE
codes, 180/200 EIN↔CCN resolved). Use the spike numbers when explaining a
data quirk conceptually; use the production numbers when stating what shipped.

**Code snippets to pull in** (already committed, don't paraphrase from
memory): `docs/spike/parse_schedule_h.py`, `packages/datapipes/datapipes/schedule_h.py`,
`packages/datapipes/datapipes/select.py` (the "select, don't sample" fix),
`packages/rules/rules/eligibility.py` (the `0`-is-a-sentinel guard).

---

## Working title + one-line hook

"Rebuilding a closed national hospital-charity database from public IRS
filings" — the hook: nobody sells this data, because nobody has to; it's
already sitting in ~2,500 tax filings a year, in a machine-readable XML
schema the IRS itself publishes, unindexed and unjoined.

## 1. The problem this pipeline solves

- Charity-care policy (who qualifies, at what income threshold, how to
  apply) is a *disclosure obligation* under 26 CFR 1.501(r)-4 — every
  nonprofit hospital has to publish it — but there's no consolidated,
  structured place to query it across hospitals.
- Frame the stakes with the product's own numbers: the 76% non-application
  rate and the fact that a hospital's own FAP thresholds decide who's
  eligible for free care outright.
- One sentence distinguishing this from a scraper: we don't scrape hospital
  websites for their policy — we parse the same government filing the IRS
  already requires them to submit, at the primary source.

## 2. Where the data actually lives

- `apps.irs.gov`'s bulk 990 XML index (`2024_TEOS_XML_11A` etc.) — Schedule H
  Part V Section B, the "Facility and Facility Reporting Groups" repeating
  element `HospitalFcltyPoliciesPrctcGrp`.
- Show the three fields that matter and their contract mapping (a small
  table, same as `docs/SPIKE.md`'s): `FPGFamilyIncmLmtFreeCarePct` →
  `free_care_max_fpl_pct`, `FAPAvailableOnWebsiteURLTxt` → `fap_url`, etc.
- Namespace and schema stability finding: one parser covers 2021v4.0 through
  2023v5.1 — cite this as evidence the approach generalizes past the demo's
  200-hospital seed.

## 3. Every data quirk that would have silently corrupted the dataset

This is the section that makes the post technical instead of promotional —
each quirk gets a short callout of *what would have gone wrong* if unhandled:

- **The zero-sentinel trap.** A hospital reporting `0%` for discounted-care
  FPL threshold means "not offered," not "0% of poverty line." Treated
  literally, it makes every patient at that hospital look ineligible for
  discounted care. Show the guard from `packages/rules/rules/eligibility.py`.
- **66.6% of the "URL" field isn't a URL.** Cross-references like "SEE PART
  V, SECTION C" outnumber real links; a naive parser would count these as
  present-but-broken data instead of routing to the free-text fallback.
- **A cheap repair pass is worth 2×.** Missing schemes
  (`WWW.SENTARA.COM/...`) and missing colons (`HTTPS//WVUMEDICINE.ORG/...`)
  take usable URLs from 31.8% to 61.2%. Show the repair function.
- **"Select, don't sample."** Random sampling of 200 hospitals from the
  batch would yield roughly a 26% live-URL rate — below the accuracy bar this
  project set for itself. Selecting candidates that already have a
  repairable URL hits 100% by construction, not luck. This is the one
  design decision in the whole pipeline most worth explaining, because it's
  a two-line code change with a real accuracy consequence.
- **The bulk files are the only path in** — the old per-filing S3 endpoint
  404s now, batches are ~1.1GB ZIP64 files that macOS's built-in `unzip`
  can't open (Python's `zipfile` can), and batch IDs are case-sensitive in a
  way the index itself isn't consistent about.

## 4. The unplanned win: EIN is in the price-transparency filename

- CMS's machine-readable-file naming convention
  (`<ein>_<hospital-name>_standardcharges.<ext>`) hands you the EIN↔hospital
  crosswalk for free, closing a join `packages/datapipes` would otherwise
  need a third-party API for.
- Real numbers: Advocate Christ Medical Center's MRF gives `$140` gross vs.
  `$70` cash for CPT 86787 — a flat 50%-of-gross discount, independently
  confirming a hospital's own published pricing is enough to prove a
  self-pay patient is being overcharged 2×, no external pricing database
  required.

## 5. What shipped, in production numbers

- 200 hospitals seeded to Firestore, **100% live FAP-URL rate** (by
  selection, per §3 above).
- EIN↔CCN crosswalk resolved for 180/200.
- 2,881 NCCI PTP pairs + 15,112 MUE codes loaded for the billing-audit side
  of the product (not this post's main subject, but worth one sentence
  since it reuses the same pipeline's infrastructure).
- One honest caveat, stated plainly: this seed is 200 hospitals, not the
  full ~2,500 that carry a usable Schedule H facility record in a given
  year — deliberate scope discipline for a 12-day build, not a claim that
  the harder problem (full national coverage) is solved.

## 6. Close

- The throughline: none of this required a private data vendor or an LLM
  guessing at a hospital's policy — it required reading the filing the
  government already collects, carefully, with the quirks accounted for.
- CTA: link to the repo (`packages/datapipes`), to `docs/SPIKE.md` for the
  receipts, and to the live demo.
- Required for scoring: publish with `#AllThingsAgenticHackathon` mentioned
  in the post itself if the platform supports tags, and cross-post the link
  in the social posts (see below) rather than only linking one direction.

---

## Companion social posts (X + LinkedIn, §1.3 bonus 0.2)

Not full copy — the angle each platform's post should take, since the tone
differs:

- **X:** lead with the single sharpest number (2× overcharge, or 76%
  non-application rate), a 15-30s demo GIF of the activity feed filling in
  live, link to the blog post, tag `#AllThingsAgenticHackathon`.
- **LinkedIn:** lead with the "why agents, not a form" framing (five legal
  clocks running at once) — this audience responds to the multi-agent
  architecture angle more than the raw stat. Link to both the blog post and
  the Devpost submission.
- Both: use a real screenshot or GIF from the actual dashboard (per
  `docs/VIDEO_SCRIPT.md`'s live-demo block), not a mockup — same honesty
  standard as the README and the video.
