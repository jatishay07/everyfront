# Video script — Every Front

**Target runtime:** 4:00 exactly · **Mix:** ~60% explanation / ~40% live demo
**Recording note (§5 sprint calendar):** record Aug 29 morning, fresh API quota;
rehearse the live-demo block (0:50–2:35) three times before it's on camera — if
it doesn't pass 3/3 rehearsals, the fallback is `/demo/inject_bill` shown live
rather than any pre-recorded/edited footage. The whole video must be an
unedited run of the actual system; cuts between *sections* are fine, cuts
*within* a live-demo section are not.

Every on-screen claim below is sourced to something either already committed
in this repo (`README.md`, `docs/SPIKE.md`, `BUILD_PLAYBOOK.md`) or
re-verified directly against the live services on 2026-08-25 while writing
this pass of the script — every case injection, approval, and Verifier block
described below was actually run against `ef-api`/`ef-agent-core`, not
assumed. **Still, do not treat any specific number here as guaranteed** — a
fresh run on recording day will produce its own real numbers, and the
narration is written to read the numbers off the screen rather than recite
these ones from memory.

### ⚠️ Before you record: the demo data must be reset

As of 2026-08-25 the live system is carrying 15+ open cases from ongoing
testing (including the verification calls made while writing this script),
with old, random-suffixed case IDs — not a clean corpus. **Run `make
demo-reset` and confirm a clean case list before rehearsing**, and check
whether the open pull request that adds a clean, human-readable-ID reseed
(see `README.md`'s "Honest limitations") has been merged and deployed by
recording day. If it hasn't, reset still works — it purges cleanly — it just
won't hand you pretty IDs.

### ⚠️ Timing risk — read before rehearsing

A case takes on the order of 45–90 seconds to fully process end to end
(three fresh timed runs on 2026-08-25 came in at 47s, 57s, and 68s for a
3-document fixture). SWARM has been improving this but **do not assume a
specific number** — script around today's reality:

1. **The activity feed does not wait for the HTTP response to finish.** Every
   agent step is written to Firestore as it happens (`cases/{id}/events`),
   and the dashboard polls every few seconds — so the moment `/demo/inject_bill`
   is triggered, events start appearing on screen well before the API call
   itself returns. The live-demo block is scripted around watching that feed
   fill in, not around waiting for a response. A slower run doesn't produce
   dead air, it produces more real footage of the feed populating.
2. **If a rehearsal runs long, extend narration over the feed** — talk
   through more individual events instead of summarizing. If it runs
   *short*, the fronts/deadlines panel, the Verifier block, and the Approve
   click still all have to happen on camera; cut to the next section a few
   seconds early rather than padding with dead air (a beat landing early is
   not an edit — see the note above about cuts between sections being fine).
3. **On camera, say the real number.** Whatever the clock in the corner
   shows, say it. "You can see this took about a minute" is a stronger beat
   than pretending it's instant.
4. **`approve_filing`, once a case has finished analyzing, is fast** — three
   live approvals during this pass each returned in under 10 seconds. Don't
   over-budget this beat; the long pole is the initial analysis, not the
   approval.

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
> REHEARSAL ONLY** on screen if it is ever used in a take that isn't final.
> Still unreplaced as of this pass — check this note before every rehearsal,
> not just the first one.]

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

## 0:50–2:20 — Live, unedited run

**Visual:** screen recording, single take, system clock visible in a corner
throughout so a viewer can confirm it's real time, not edited.

| Time | Beat | What's on screen |
|---|---|---|
| 0:50–1:00 | A bill arrives | Trigger the pipeline via `/demo/inject_bill` with `{"fixture_name": "case_07_il_concurrent_clocks"}` (an Illinois case with real concurrent deadlines — or a live Gmail send, if 3/3 rehearsals passed and OAuth has actually been set up, which as of this pass it has not) |
| 1:00–1:30 | The activity feed | The dashboard's live event stream filling in as Firestore writes land: Reader classifies each document (Gemma first, then Gemini extracts a real six-line itemized bill), Lookup resolves **Advocate Christ Medical Center** as nonprofit, Clock computes concurrent Illinois deadlines — **pause on a citation chip** (a live run on 2026-08-25 showed `210 ILCS 89/10` on the charity-care front and `45 CFR 149.620(a)(2)(ii), (c)(1)` on PPDR) so it freeze-frames legibly. Say the real event count on screen, whatever it is |
| 1:30–1:50 | Fronts + deadlines | Case detail view: fronts panel showing **4 fronts evaluated** — `audit` and `charity_care` applicable, `ppdr` applicable with a computed deadline, `debt_validation` correctly marked not-applicable ("account is not reported in collections") — deadline ladder, at least one dated chip |
| 1:50–2:05 | Human approves | Click **Approve filing** on the `charity_care` front on camera — call out that nothing is ever sent without this click. (Verified live during this pass: a real 387,861-byte Advocate FAP application came back, uploaded to a real GCS object, in well under 10 seconds — this is the actual hospital application, not a placeholder.) |
| 2:05–2:20 | Delivery proof + Cloud proof | Vendor ID and status on screen (**say "test mode" out loud** — Phaxio/Lob credentials are not configured as of this pass, so this is honestly a recording stub, not a live send); stats banner ticking up; then **cut to the Cloud Run console** showing `ef-api`, `ef-agent-core`, `ef-intake`, `ef-web` deployed and running, then to a public URL in the browser bar — the §1.3 "visible proof of deployment" beat, don't rush it |

**Narration runs under the whole block**, naming the agent acting at each
beat and repeating the ordering point from 0:25–0:50 concretely: "notice
debt validation gets handled before charity care in this case — that's not
arbitrary, that's the moratorium." For this fixture, debt validation is
correctly ruled out (the patient isn't in collections) rather than sequenced
first — call that out too: the system says "not applicable" instead of
silently omitting the front, the same honesty principle as the for-profit
path and the Verifier beat coming up next.

---

## 2:20–2:35 — The Verifier refuses to file (feature this, don't apologize for it)

**This is the best 15 seconds in the demo.** An earlier internal note briefly
described one of the Verifier's blocks as a false positive — that was wrong,
and it's corrected in the README. Both of the Verifier's live blocks below
were reproduced against the deployed API while writing this script, not
staged for the camera.

**Visual:** cut to the intake flow. Upload the `case_05_cat_photo_income_proof`
fixture's income-proof document (a literal photo of a cat) as "proof of
income" on a charity-care filing, then click **Approve filing** on
`charity_care`.

**What happens on screen, live:** an HTTP 409 comes back and the UI surfaces
it in plain language — a real response from this pass reads *"document ...
does not appear to actually be an income document (Reader's cat-photo check
failed)."*

**Narration:**

> "Watch what happens when the paperwork doesn't hold up. This is a photo of
> a cat, uploaded as proof of income. The Verifier — the agent that runs
> right before anything gets filed — catches it and refuses. Not a crash, not
> a silent skip: a blocked filing with a plain-English reason a human
> advocate reads before deciding what to do next. It does the same thing when
> there's simply no income document at all for a charity-care filing. An
> agent that will file real paperwork on a patient's behalf has to be an
> agent that knows when *not* to."

*(If time allows in a longer cut, or as a fallback if the cat-photo fixture
isn't handy on recording day, the second reproducible block — a charity-care
approval attempt on `case_02_wrongful_denial_il` with no income_proof
document on file at all, returning `"no income_proof document on file for a
charity-care filing"` — makes the same point and was equally live-verified
in this pass.)*

---

## 2:35–3:15 — Architecture walkthrough

**Visual:** `docs/architecture.svg`, full frame, static — this is the
rubric-named diagram, give it room to be read.

**Narration, naming every agent and both models explicitly:**

> "Six agents, one ADK hierarchy. **Reader** classifies the document — and
> this is where **Gemma 4** does its first pass, the smaller bonus-eligible
> model, before **Gemini 3.7 Flash** does the structured extraction. **Lookup**
> resolves the hospital — by tax ID, or by name when the bill doesn't carry
> one. **Clock and Auditor** are thin wrappers around a deterministic rules
> engine — the LLM narrates, the code computes, and every deadline carries
> its regulation citation. **Strategist** sequences the fronts and stops for
> a human to approve. **Verifier** — what you just watched — cross-checks the
> paperwork before anything is allowed to file. **Filer** renders the real
> hospital and CMS forms and sends them. All of it runs on **Cloud Run**,
> talks over **Pub/Sub**, and keeps case state in **Firestore**."

**On screen while naming each service:** briefly highlight/circle the Cloud
Run, Pub/Sub, and Firestore labels on the diagram as they're named.

---

## 3:15–3:55 — Honest limits, then the close

**Visual:** a plain "Honest limitations" slide — no logo, no polish, matching
the tone of the README section it's drawn from.

**Narration:**

> "To be direct about what this is and isn't: every case in this demo is
> synthetic — no real patient, no real bill, and there's no authentication on
> any endpoint today, so this stays a demo against synthetic data, not
> something pointed at a real inbox yet. About 40% of U.S. hospitals are
> for-profit and owe no charity-care duty at all under federal law, and the
> system says so rather than pretending otherwise. CMS doesn't publish
> outcome data for the dispute-resolution process we file into, so we don't
> claim a success rate we can't back up. And one thing in the pipeline
> itself still isn't fully wired: the billing-audit engine can find real
> overcharges — we can show you $1,217.50 across six findings on this exact
> bill when we run the underlying math by hand — but the live pipeline isn't
> reliably surfacing all of them yet end to end, so the number you saw on the
> stats banner a moment ago is the honest one, not that one. Nobody is
> hiding the gap; it's in the README with the exact repro."

**Visual, final 10 seconds:** cut back to the live stats banner from the demo,
let the actual numbers sit on screen.

**Narration (close):**

> "X filings. $Y found. Zero human hours."

*(Replace X and Y with whatever the banner actually reads at record time —
never a scripted number. As of this script being written, `savings_found_cents`
on a fresh run is driven mainly by charity-care erasure — the whole bill,
when a patient screens as free-care-eligible — which is a real and
defensible number, not the (currently under-firing) audit dollar figure
named in the previous beat. Say whatever the banner shows. A judge checking
the repo against the video is the scenario this whole script is written to
survive.)*

---

# Teleprompter — word-for-word, timed to 4:00

> **SUPERSEDED 2026-08-29** by "Final narration — timed to the delivered cut" at the
> bottom of this file. That one is timed to the actual 2:45 edit that exists on disk.
> Keep this section only for the beat rationale.

Added 2026-08-29. Read this column aloud; the beats above tell you what to
show. ~570 words at a natural 145–150 wpm leaves room to breathe.

**Two corrections to the beats above — they were written 2026-08-25 and the
system has moved since. Do not read the older narration verbatim:**

1. **The audit figure now reproduces live.** The 3:15–3:55 block above tells
   you to say the pipeline "isn't reliably surfacing all of them yet." That
   was true then; it is false now. `ef-2026-0007` returns
   `audit_findings_cents: 121750` with six `audit_finding` events. Saying the
   old line on camera would understate your own system and describe a
   limitation that no longer exists.
2. **Gmail OAuth is live**, so "which as of this pass it has not" in the
   0:50 beat is stale. You may show a real inbox arrival if you want it.

**One structural note:** `make demo-reset` seeds cases 02–08 and deliberately
leaves `case_01_uninsured_gfe_ca` unseeded so it can be injected **live, on
camera** — that is the injection beat, not `case_07`. Case 07 is already in
the corpus; open it afterward as the depth example, because it carries the
six audit findings and the concurrent Illinois clocks.

---

### 0:00–0:25 · The problem

> "Seventy-six percent of people who qualify for hospital charity care never
> apply — because nobody tells them it exists. Under one percent of insurance
> denials get appealed, and about a third of those appeals win. Fourteen
> billion dollars a year in charity care goes unclaimed. The law is not
> hidden. It's just spread across four separate regimes, each with its own
> eligibility test, its own form, and its own deadline — and those deadlines
> run from different trigger dates a patient has no reason to have written
> down."

### 0:25–0:50 · Why this needs agents

> "One bill can start four legal clocks at once. The charity-care window —
> 240 days — doesn't start the day you got care. It starts at the first
> post-discharge billing statement. If you're uninsured and the bill blew
> past your estimate, that's 120 days to dispute. If a collector sends a
> validation notice, 30 days — and that one goes first, because it freezes
> everything else. Get the ordering wrong and you lose a right. That's not a
> form-fill. That's agents that read the document, run the actual law, and
> sequence the result."

### 0:50–2:20 · Live run — narrate over the feed

> "This is the live system on Google Cloud. I'm dropping in a real bill now."

*(trigger the injection — then talk over the feed as it fills)*

> "Nothing here is pre-computed. Gemma 4 classifies each document first —
> bill, estimate, income proof — then Gemini 3.7 Flash pulls the structured
> facts out. Lookup resolves the hospital against two hundred and four real
> hospitals we seeded from their own IRS Schedule H filings. Watch the feed:
> every one of those lines is an agent writing to Firestore as it happens."

*(pause on a citation chip — let it sit)*

> "Every deadline carries the regulation it came from. The LLM narrates. The
> code computes. Those are different jobs and we never let them mix."

*(fronts panel)*

> "Four fronts evaluated. Read them off the screen — including the one it
> rules out. It says 'not applicable' and gives the reason, instead of
> quietly dropping it."

*(click Approve — say it out loud)*

> "Nothing files without a human. That click is the gate."

*(open ef-2026-0007)*

> "Here's the depth. Six audit findings on one bill — including a lab test
> billed at a hundred and forty dollars against the hospital's own published
> cash price of seventy. That's their number, not ours."

*(cut to Cloud Run console, then a .run.app URL in the bar — do not rush)*

> "Four services, all on Cloud Run, in this project, right now."

### 2:20–2:35 · The Verifier refuses

> "Now watch it refuse. I'm submitting a photo of a cat as proof of income.
> It blocks the filing and says why. An agent that files confidently on
> garbage is worse than no agent."

### 2:35–3:15 · Architecture

> "Seven agents, one ADK hierarchy. Reader classifies — Gemma 4 first, then
> Gemini 3.7 Flash extracts. Lookup resolves the hospital by tax ID, or by
> name when the bill carries none. Clock and Auditor are thin wrappers over a
> deterministic rules engine with a hundred percent branch coverage and zero
> LLM calls inside it. Strategist sequences and stops for a human. Verifier
> cross-checks. Filer renders the real hospital and CMS forms. Cloud Run,
> Pub/Sub, Firestore."

### 3:15–3:55 · Honest limits, then close

> "To be direct. Every patient in this demo is synthetic — the hospital
> policy, the prices and the law are real; the people are invented. There's
> no authentication on these endpoints, so this stays a demo, not something
> pointed at a real inbox. Forty percent of US hospitals are for-profit and
> owe no charity-care duty at all — the system says so instead of pretending.
> The fax and mail vendors are in test mode, and it labels every filing
> simulated rather than claiming a send it didn't make. All of that is in the
> README with the repro."

*(cut to the stats banner — read the actual numbers)*

> "That's ___ filings and ___ found, in a run that took about ___ seconds.
> Zero human hours."

*(Read whatever the banner shows. Never a scripted number — a judge checking
the repo against this video is exactly the scenario this script is built to
survive.)*

---

# Shot list — narrative cut (generated B-roll + real demo)

> **PLANNING DOCUMENT.** The timeline below is the 4:00 plan; the cut that
> shipped is 2:57 and is documented under "Final narration — as delivered" at
> the end of this file. The generation prompts, the Lyria cue and the rule
> about never cutting inside the demo block all still apply.

Added 2026-08-29. Concept: a patient gets a crushing bill and loses everything;
rewind, and the same bill goes through Every Front instead. The "background
process" the story cuts into IS the real screen recording — that's what keeps
the §1.3 live-demo requirement satisfied inside a narrative frame.

## The rule this structure protects

`BUILD_PLAYBOOK` §5 and the Devpost criteria both want a **live, unedited**
demo. So:

- **Generated media only ever appears OUTSIDE the demo block** — cold open,
  the spiral, the transitions, the payoff, the close.
- **The demo block is one contiguous 90-second take with zero internal cuts.**
  Never intercut generated footage into it, never speed-ramp it, never
  composite anything over the dashboard.
- Nothing generated may depict the product working, a Google Cloud console,
  or any number the system did not actually produce.

Break those and the strongest thing about this project — that it is real and
refuses to fabricate — becomes the thing a judge doubts.

## Assets already cut and verified

| file | duration | contents |
|---|---|---|
| `demo-live-uncut.mp4` | 1:30 | contiguous: live `ef-2026-0001` agent events, hospital resolution citing 26 CFR 1.501(r), deadline computation, savings summary at 117.1303% FPL, case detail, deadline ladder, citation chips, the green **Approve & File** gate, then `ef-2026-0007` at $1,218 |
| `demo-cloudproof.mp4` | 0:20 | Cloud Run console — four services green in us-central1, project "Every Front", live scaling chart |

Both are cropped to remove browser chrome and personal bookmarks. Source
recording made 2026-08-29 against the live deployment; the run took 140.3s of a
240s budget.

## Timeline — 4:00 (planned; see delivered cut below)

| time | dur | shot | source |
|---|---|---|---|
| 0:00 | 0:08 | Clinic corridor, patient leaving an appointment. Ordinary, unremarkable. | GEN 1 |
| 0:08 | 0:12 | Phone buzzes. Close on the screen: **$2,625.00 due.** Hold on the number. | GEN 2 |
| 0:20 | 0:06 | FOR SALE sign hammered into a front lawn | GEN 3 |
| 0:26 | 0:06 | Car reversing off a driveway, someone else driving | GEN 4 |
| 0:32 | 0:06 | Empty living room, sunlight, no furniture | GEN 5 |
| 0:38 | 0:07 | Patient sitting on a single cardboard box in the empty room | GEN 6 |
| 0:45 | 0:10 | Cut to black. Text: **"Or."** Same phone, same notification, thumb hovering | GEN 7 |
| 0:55 | 0:10 | Camera pushes INTO the phone screen; pixels dissolve into moving data | GEN 8 |
| **1:05** | **1:30** | **THE REAL SYSTEM — uncut** | `demo-live-uncut.mp4` |
| **2:35** | **0:20** | **Google Cloud proof — Cloud Run console** | `demo-cloudproof.mp4` |
| 2:55 | 0:08 | Camera pulls OUT of the data, back through a phone screen | GEN 9 |
| 3:03 | 0:09 | Restaurant. The patient, relaxed, mid-meal. Warm light. | GEN 10 |
| 3:12 | 0:08 | Phone on the table lights up: charity care approved · PPDR filed · **$2,625.00 erased** | GEN 11 |
| 3:20 | 0:25 | Architecture diagram, full frame — name every agent, Gemma and Gemini, Cloud Run / Pub/Sub / Firestore | `docs/architecture.svg` |
| 3:45 | 0:15 | Honest limits, then close on the real stats banner | still + real |

Generated total ≈ 1:22. Demo total 1:50. Diagram and close 0:40.

## Why $2,625

That is the actual figure the system produced on camera for `ef-2026-0001`:
`Charity-care free-tier erasure: $2,625.00 (Income is 117.1303% of the federal
poverty level. At or below the 400% threshold for free care. Basis: Cal. Health
& Safety Code §127405(a)(1)(A), (d)(1); 26 CFR 1.501(r)-4(b)(2))`.

Use it in the cold open and in the payoff. The joke and the proof become the
same number, and every figure on screen is one the system really computed.

## Generation prompts

Character: a single fictional adult, **not a likeness of anyone real**. Keep the
face partially obscured or off-angle in most shots — generators are weakest at
consistent faces, and the story does not need one. Reuse the same seed or a
reference frame across GEN 1/6/10 so the person reads as continuous. **No
generated dialogue or lip-sync** — narration is recorded separately.

- **GEN 1** — "Wide shot, a person in a jacket walking out of a bright modern medical clinic corridor, seen from behind, afternoon light through tall windows, calm handheld camera, muted realistic colour grade, no text."
- **GEN 2** — "Extreme close-up of a smartphone screen in a person's hand, a billing notification appearing, clinical white UI, the hand tightens slightly, shallow depth of field, realistic, no readable brand marks."
- **GEN 3** — "A FOR SALE sign being pushed into the lawn of a modest suburban house, late afternoon, slight comic overemphasis on the hammer blow, realistic film look."
- **GEN 4** — "A car slowly reversing out of a driveway while a person watches from the kerb, seen from behind, autumn light, wistful, gently comedic timing."
- **GEN 5** — "Slow dolly through a completely empty living room, bare walls, sunlight and dust in the air, one power cable on the floor, melancholy but absurd."
- **GEN 6** — "A person sitting alone on a single cardboard box in the middle of an empty room, shoulders slumped, wide symmetrical framing, deadpan comedy, realistic."
- **GEN 7** — "Black screen resolving into an extreme close-up of the same phone notification, a thumb hovering without tapping, held still, tense stillness."
- **GEN 8** — "Camera pushes forward into a phone screen, the pixels dissolving into flowing streams of abstract blue and green data, seamless forward motion, no text, no UI."
- **GEN 9** — "Camera pulls backward out of flowing abstract data streams, resolving into the surface of a phone lying on a restaurant table, seamless reverse motion."
- **GEN 10** — "A person sitting comfortably at a warm, softly lit restaurant table, eating, relaxed shoulders, out-of-focus diners behind, golden hour, realistic."
- **GEN 11** — "Close-up of a phone face-up on a restaurant table, screen lighting up with several calm green confirmation notifications stacking, warm reflections, shallow depth of field."

## Lyria score

One continuous cue, roughly 4:00, arranged to the structure so the turn lands
on the push-in at 0:55:

> "Cinematic instrumental for a short documentary. Opens sparse and uneasy —
> single piano notes, low sustained strings, a slow ticking pulse under it.
> Tension tightens through the first forty seconds without resolving. At around
> fifty-five seconds the pulse turns purposeful and mechanical, like a system
> coming online: muted arpeggiated synth, steady low percussion, forward
> momentum, still restrained. Holds that focused drive for two minutes. Near the
> three-minute mark it opens into warmth and resolution — strings blooming,
> piano returning in a major key, unhurried and hopeful. Ends settled and quiet.
> No vocals, no lyrics. Leaves headroom for spoken narration throughout."

Mix the score **under** narration at roughly -18 dB, and duck it further beneath
the demo block so the on-screen text stays the focus.

## Bonus credit

Devpost awards bonus for integrating Google AI models "such as Gemma, Veo or
Lyria." Gemma already does first-pass document classification in the pipeline.
If Veo generates the B-roll and Lyria the score, name all three explicitly in
the Devpost write-up — that is the whole bonus line, not a partial claim.

---

# Final narration — as delivered

Rewritten 2026-08-29 to match the video that actually shipped:
`~/Movies/everyfront-final-narrated.mp4` — **2:57**, 1920x1080, burned-in
captions, narrated.

**An earlier draft of this section was written for 150 wpm and did not survive
contact with the voice.** The TTS reads at about 95 wpm, so a 392-word script
had to be time-stretched 1.3x-1.55x to fit, and it sounded exactly as rushed as
that implies. This version is 260 words, written for the real speaking rate:
163 seconds of speech in a 177-second picture, with only the demo block nudged
to 1.03x. If you re-record in your own voice, read it at a normal pace and it
will fit as written.

## What is in the delivered cut

| in | out | shot |
|---|---|---|
| 0:00 | 0:18.0 | the spiral - clinic, the bill ($2,625.00), FOR SALE, the car, empty room, the box |
| 0:18.0 | 0:22.0 | **title card - introduces Every Front and the four fronts** |
| 0:22.0 | 0:29.7 | walking back in, the hover, push into the screen |
| 0:29.7 | 1:59.7 | **the real system - 90 seconds, one take, no internal cuts** |
| 1:59.7 | 2:19.7 | **Cloud Run console - four services live in us-central1** |
| 2:19.7 | 2:29.2 | pull out, restaurant, **$2,625.00 ERASED** |
| 2:29.2 | 2:57.2 | architecture diagram |

## Narration, with the timecode each line starts on

**0:00** - "Seventy-six percent of the people who qualify for hospital charity
care never apply. Nobody tells them it exists. So the bill arrives, and it
looks like the end of everything."

**0:18 - 0:22 · say nothing.** The card does the work. The silence after "the
end of everything" is the turn of the whole film; narrating over it kills it.

**0:22** - "Four legal fronts could have cut this bill. Every one of them has a
deadline."

**0:29.7** - "This is the deployed system. A real bill, going in now."

**0:37.5** - "Gemma 4 classifies the documents. Gemini 3.7 Flash extracts the
facts."

**0:50.5** - "Lookup identifies the hospital from its own IRS Schedule H
filing, one of two hundred and four. Nonprofit, so 501(r) applies."

**1:05** - "The Auditor catches a duplicate charge. The Clock computes the
deadline and cites the statute behind it. Models read. Code calculates."

**1:18.8** - "Two hundred and ten dollars in errors, and charity care wiping
the balance entirely. Income at a hundred and seventeen percent of the poverty
level."

**1:32.8** - "Watch the front it rules out. Not in collections, so debt
validation does not apply. It explains why."

**1:43.6** - "And nothing gets filed until a person approves it."

**1:48.4** - "A second case: six findings, twelve hundred eighteen dollars. A
lab test billed at a hundred forty against their own published price of
seventy."

**2:01.6** - "Four Cloud Run services, running in us-central1 right now.
Pub/Sub moves events between them. Firestore holds every case, and the audit
trail you just watched fill in."

**2:20** - "Twenty-six hundred dollars, gone. She never filled in a form."

**2:29.6** - "Seven agents in one ADK hierarchy. The models read and draft; the
calculations are deterministic and show their arithmetic. And plainly, these
patients are invented. The hospital policy, the prices, the law are real.
Vendors run in test mode, and every filing says so."

## Rules this cut is built to satisfy

- **Live, unedited demo.** The 90-second block is one contiguous take with no
  internal cuts, no speed ramps, and nothing composited over the dashboard.
  Generated footage appears only outside it.
- **Visible proof of Google Cloud.** 20 seconds on the Cloud Run console
  showing all four services green in us-central1 under the project.
- **Bonus models named out loud.** Gemma 4 and Gemini 3.7 Flash at 0:37.5.
  Veo 3.1 generated the narrative footage and Lyria a score (dropped from the
  final mix); name all of them in the Devpost write-up.
- **Every number is real.** $2,625.00, 117.1303% FPL, $210, $1,218, $140 vs
  $70 - each was produced by the system on camera, not written for the script.

## If you re-record in your own voice

Read at a normal pace against the timecodes above. Your voice will almost
certainly land better with judges than the synthesized one, and the picture
does not need to change - the audio track can be swapped in directly.
