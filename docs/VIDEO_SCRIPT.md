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
improvise a number while recording. The specific numbers in the live-demo
block below (30 events, 4 fronts, the 2026-10-03 PPDR deadline) are from a
real `/demo/inject_bill` call against the live `ef-api`/`ef-agent-core`
services on 2026-08-25 — see `README.md`'s "The pipeline works end to end"
section for the transcript. **Do not treat them as guaranteed** — a fresh run
on recording day will produce its own real numbers (probably close, since
nothing about the fixture or the rules changes), and the narration below is
written to read the numbers off the screen rather than recite these ones from
memory.

### ⚠️ Timing risk — read before rehearsing

A case currently takes on the order of a couple of minutes to fully process
(our own recorded run of `case_01_uninsured_gfe_ca` took 75 seconds for its 3
documents; other cases in the corpus have run closer to ~2.5 minutes end to
end). SWARM is actively working on latency, but **do not assume it will be
fixed by Aug 29** — script around today's reality:

1. **Pick `case_01_uninsured_gfe_ca` for the recording.** It's the fixture we
   actually timed (75s, 3 documents, 30 events, all 4 fronts evaluated), it's
   the "safe" no-deadline-drama-but-still-a-real-deadline CA case, and it's
   the one this script's numbers come from.
2. **The activity feed does not wait for the HTTP response to finish.** Every
   agent step is written to Firestore as it happens (`cases/{id}/events`),
   and the dashboard polls every few seconds — so the moment `/demo/inject_bill`
   is triggered, events start appearing on screen well before the API call
   itself returns. The live-demo block is scripted around watching that feed
   fill in, not around waiting for a response. This means a slower run doesn't
   produce dead air, it just produces more real footage of the feed
   populating — which is the point of this beat.
3. **If a rehearsal runs long, extend narration over the feed rather than
   rushing it** — talk through more of the individual events instead of
   summarizing. If it runs *short*, the fronts/deadlines panel and the
   Approve click still have to happen on camera; don't pad with dead air, cut
   to the next section a few seconds early instead (a beat landing early
   is not the same as an edit — see 5:2 note above about cuts between
   sections being fine).
4. **On camera, say the real number.** If a case takes 90 seconds instead of
   75, or 3 minutes instead of 75 seconds, say so — "you can see this is
   taking about two minutes; we're actively working on that" is a stronger
   beat than pretending it's instant, and it pre-empts a judge wondering why
   the clock in the corner doesn't match a script that promised something
   faster.

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
| 0:50–1:05 | A bill arrives | Trigger the pipeline via `/demo/inject_bill` with `{"fixture_name": "case_01_uninsured_gfe_ca"}` (or a live Gmail send, if 3/3 rehearsals passed) |
| 1:05–1:35 | The activity feed | The dashboard's live event stream, filling in in real time as Firestore writes land: Reader classifies each document (Gemma first, then Gemini extracts), Lookup resolves **Sutter Bay Hospitals** as nonprofit, Clock computes each deadline — **pause on a citation chip** (our own run showed `26 CFR 1.501(r)-1(b)(29)(i)` on the Lookup step and `45 CFR 149.620(b), (c)` on the PPDR front) so it freeze-frames legibly. Our test run logged 30 events end to end — say the real count on screen, whatever it is |
| 1:35–1:55 | Fronts + deadlines | Case detail view: fronts panel showing all **4 fronts evaluated** — `audit` and `charity_care` applicable, `ppdr` applicable with a computed deadline, `debt_validation` correctly marked not-applicable ("account is not reported in collections") — deadline ladder, at least one dated chip (our run computed the PPDR deadline as **2026-10-03**) |
| 1:55–2:10 | Human approves | Click **Approve filing** on camera — call out that nothing is ever sent without this click |
| 2:10–2:25 | Delivery proof | Phaxio/Lob vendor ID and status on screen; Calendar entry with the citation in its description; stats banner numbers tick up live. *If recording before live vendor credentials are configured, say so on camera* — "this is Phaxio's test mode" is honest, a silent cut to make it look like a live send is not |
| 2:25–2:40 | **Cloud proof** | **Cut to the Cloud Run console** showing `ef-api`, `ef-agent-core`, `ef-intake`, and `ef-web` deployed and running, then to the public URL in a browser address bar: `https://ef-agent-core-756591166292.us-central1.run.app` (and/or `https://ef-api-756591166292.us-central1.run.app`) — this is the §1.3 "visible proof of Google Cloud deployment" beat, don't rush it |

**Narration runs under the whole block**, naming the agent that's acting at
each beat and repeating the ordering point from 0:25–0:50 concretely: "notice
debt validation gets handled before charity care in this case — that's not
arbitrary, that's the moratorium." For this specific fixture, debt validation
is correctly ruled out (the patient isn't in collections) rather than
sequenced first — call that out too: the system says "not applicable" instead
of silently omitting the front, which is the same honesty principle as the
for-profit-hospital path.

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
> process we file into, so we don't claim a success rate we can't back up.
> And two things in the pipeline itself aren't fully wired yet: hospital
> lookup today matches on the tax ID in the bill, not the hospital's name, and
> the dollar figure for billing-audit findings reads zero in this build,
> because the extraction step doesn't hand the audit engine line items yet —
> the audit math underneath is fully tested, it just isn't getting fed. Both
> are being fixed in parallel with this recording."

**Visual, final 10 seconds:** cut back to the live stats banner from the demo,
let the actual numbers sit on screen.

**Narration (close):**

> "X filings. $Y found. Zero human hours."

*(Replace X and Y with whatever the banner actually reads at record time —
never a scripted number. As of this script being written, a real run reads
`audit_findings_cents: 0` for the reason just named on the honest-limits
slide — if that is still true on recording day, say "zero" on camera and let
the previous beat explain why, rather than picking a different case to hide
it. If SWARM's fix lands before Aug 29 and the number is real, say that
number. A judge checking the repo against the video is the scenario this
whole script is written to survive.)*
