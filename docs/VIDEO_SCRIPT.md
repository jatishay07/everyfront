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
