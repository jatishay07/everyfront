# Video script — Every Front

**Target runtime:** 4:00 exactly · **Mix:** ~60% explanation / ~40% live demo
**Recording note (§5 sprint calendar):** record Aug 29 morning, fresh API quota;
rehearse the live-demo block (0:50–2:40) three times before it's on camera — if
it doesn't pass 3/3 rehearsals, the fallback is `/demo/inject_bill` shown live
rather than any pre-recorded/edited footage. The whole video must be an
unedited run of the actual system; cuts between *sections* are fine, cuts
*within* the live-demo section are not.

Every on-screen claim below is sourced to something already committed in this
repo (`README.md`, `docs/SPIKE.md`, `BUILD_PLAYBOOK.md`) so nobody has to
improvise a number while recording.

---

## 0:00–0:25 — The problem

**Visual:** cold open, a single stat card on screen, no logo yet.

**Narration:**

> "76% of patients who qualify for free hospital care never apply for it —
> because nobody tells them it exists. Less than 1% of medical billing denials
> are ever appealed. When someone does appeal, they win about a third of the
> time. Roughly $14 billion a year in charity care that hospitals are legally
> required to offer goes unclaimed."

**On screen, as each number is spoken:** `76%` → `<1% appealed, 34% win` →
`$14B/yr unclaimed`.

**Patient quote (0:20–0:25):**

> [PRODUCER NOTE — do not record this beat until this line is replaced with a
> real, sourced quote (a named patient-advocacy interview, a news story, or a
> quote obtained with consent) and the source is added to this file. We are
> not putting words in a real patient's mouth without a citation, and no real
> patient story exists anywhere in this repo — `fixtures/` is 100% synthetic.
> For rehearsal only, a placeholder that reflects a sentiment reported
> repeatedly in charity-care journalism: "I didn't find out I qualified for
> free care until collections had already started." Mark it **SYNTHETIC —
> REHEARSAL ONLY** on screen if it is ever used in a take that isn't final.]

---

## 0:25–0:50 — Why this genuinely needs agents

**Visual:** a simple timeline graphic, five clocks starting at different
points, ticking down at different rates, converging.

**Narration:**

> "This isn't one form. A single bill can start five legal clocks at once, and
> they interact. The charity-care window — 240 days — doesn't start on the day
> you got care, it starts on the first *post-discharge billing statement*, a
> distinction most patients never learn. If you're uninsured and the bill blew
> past your estimate, you get 120 days to dispute it. If a collector sends a
> validation notice, you get 30 days to respond — and that one has to be
> handled *first*, because it freezes everything else. Get the ordering wrong
> and you lose a right. That's not a form-fill problem. That's a job for
> agents that read the document, run the actual law, and sequence the result."

---

## 0:50–2:40 — Live, unedited run

**Visual:** screen recording, single take, system clock visible in a corner
throughout so a viewer can confirm it's real time, not edited.

| Time | Beat | What's on screen |
|---|---|---|
| 0:50–1:05 | A bill arrives | Trigger the pipeline via `/demo/inject_bill` (or a live Gmail send, if 3/3 rehearsals passed) using a fixture, e.g. `maria_uninsured_ca` |
| 1:05–1:35 | The activity feed | The dashboard's live event stream: Reader classifies the document, Lookup resolves the hospital, Clock/Auditor computes each deadline — **pause on a citation chip** (e.g. `26 CFR 1.501(r)-4(b)(1)(iv)`) so it freeze-frames legibly |
| 1:35–1:55 | Fronts + deadlines | Case detail view: fronts panel (charity care / PPDR / debt validation / audit), deadline ladder, at least one red ≤7-day chip |
| 1:55–2:10 | Human approves | Click **Approve filing** on camera — call out that nothing is ever sent without this click |
| 2:10–2:25 | Delivery proof | Phaxio/Lob vendor ID and status on screen; Calendar entry with the citation in its description; stats banner numbers tick up live |
| 2:25–2:40 | **Cloud proof** | **Cut to the Cloud Run console** showing the deployed services, then to the public URL in a browser address bar: `https://ef-agent-core-756591166292.us-central1.run.app` — this is the §1.3 "visible proof of Google Cloud deployment" beat, don't rush it |

**Narration runs under the whole block**, naming the agent that's acting at
each beat and repeating the ordering point from 0:25–0:50 concretely: "notice
debt validation gets handled before charity care in this case — that's not
arbitrary, that's the moratorium."

---

## 2:40–3:20 — Architecture walkthrough

**Visual:** `docs/architecture.svg`, full frame, static — this is the
rubric-named diagram, give it room to be read.

**Narration, naming every agent and both models explicitly:**

> "Six agents, one ADK hierarchy. **Reader** classifies the document — and
> this is where **Gemma 4** does its first pass, the smaller bonus-eligible
> model, before **Gemini 3.7 Flash** does the structured extraction. **Lookup**
> resolves the hospital. **Clock and Auditor** are thin wrappers around a
> deterministic rules engine — the LLM narrates, the code computes, and every
> deadline carries its regulation citation. **Strategist** sequences the
> fronts and stops for a human to approve. **Verifier** cross-checks the
> paperwork before anything is allowed to file. **Filer** sends it and records
> proof. All of it runs on **Cloud Run**, talks over **Pub/Sub**, and keeps
> case state in **Firestore**."

**On screen while naming each service:** briefly highlight/circle the Cloud
Run, Pub/Sub, and Firestore labels on the diagram as they're named.

---

## 3:20–4:00 — Honest limits, then the close

**Visual:** a plain "Honest limitations" slide — no logo, no polish, matching
the tone of the README section it's drawn from.

**Narration:**

> "To be direct about what this is and isn't: every case in this demo is
> synthetic — no real patient, no real bill. This is not HIPAA-compliant.
> About 40% of U.S. hospitals are for-profit and owe no charity-care duty at
> all under federal law, and the system says so rather than pretending
> otherwise. CMS doesn't publish outcome data for the dispute-resolution
> process we file into, so we don't claim a success rate we can't back up."

**Visual, final 10 seconds:** cut back to the live stats banner from the demo,
let the actual numbers sit on screen.

**Narration (close):**

> "X filings. $Y found. Zero human hours."

*(Replace X and Y with whatever the banner actually reads at record time —
never a scripted number. If the banner reads zero on some fields because a
work order slipped, say the true number on camera; a judge checking the repo
against the video is the scenario this whole script is written to survive.)*
