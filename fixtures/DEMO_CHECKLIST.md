# Demo recording checklist

## STOP -- read this before scheduling a recording session

**WO4's "twice in a row" acceptance currently FAILS live, reproducibly, as of
2026-08-26.** A real `make demo-run` this session injected the flagship
fixture successfully (beats 1-2 fine) but exited `BLOCKED: not every approved
front reached status=filed` at beat 3/4 -- confirmed via direct Firestore
inspection to be a REAL backend race, not a fluke of this one run: when 2+
fronts on the same case are approved close together (exactly what the
flagship fixture does -- audit + charity_care + ppdr, and what a live
recording's beat 3 does on camera), their asynchronous Filer runs
(`services/agent-core/agent_core/pipeline.py`'s `run_filer`,
`.../store.py`'s `upsert_front`) race on the case's single shared `fronts[]`
array via a non-atomic read-modify-write, and the loser's real, sent filing
gets silently erased from the case's own status field (though a genuine
`filings/{filing_id}` record with `status: "sent"` still exists -- the
filing itself is real; only the case's on-screen status is wrong).
Reproduced 3-for-3 today across `ef-2026-0001` (the flagship), `ef-2026-0003`,
and `ef-2026-0007`. Full reproduction + root cause + a data-repair script for
the 3 currently-affected cases are in this PR's HANDOFF -- **do not schedule
a real recording session until this is fixed and a `make demo-reset && make
demo-run` cycle has actually passed twice in a row again post-fix.** Treat
everything else in this checklist as ready; this is the one open blocker.

PROOF (persona 7), WO4 + WO6 task 3 + WO8 task 2. Paper checklist for the
recording session (BUILD_PLAYBOOK.md §5: rehearsals ×3, then record). Print
it or keep it on a second monitor -- it is not meant to be read by the
agents, and it is deliberately NOT a script: MEGAPHONE's `docs/VIDEO_SCRIPT.md`
owns the narrative (what to say). This owns the mechanics (what to click,
what to have open, what should appear on screen and roughly when, and what
to do if a beat runs long).

## WO8 update -- what changed since the last rehearsal pass

- The stuck-front / approve-timeout bug is fixed and deployed (commit
  `ca9fd40`, live as of the Cloud Run revision this checklist was last
  re-timed against). **The filing mechanics changed, not just the speed:**
  `POST /cases/{id}/approve_filing` used to run Verifier AND Filer
  synchronously in one HTTP call (real PDF render + GCS upload + vendor
  round-trip, all before the client saw a response) -- now it runs Verifier
  only, publishes `filing.requested`, and returns as soon as Verifier
  passes. Filer runs asynchronously off Pub/Sub push. **On screen this means
  the Approve click itself now visibly resolves almost immediately** (low
  single-digit seconds), followed by a short separate wait -- a few seconds
  up to roughly a minute -- before the front's status flips to `filed` and
  the filing proof appears. Don't narrate the click and the "filed" status
  as the same instant; they are two beats now, not one.
- Injecting a fixture through the live pipeline (Reader -> Lookup -> Clock ->
  Auditor -> Strategist, all for real) is now observed at roughly **45-90
  seconds**, not the ~130s this checklist previously cited -- re-time your
  own dry run regardless; it will drift again as the pipeline changes.
- `fixtures/demo_reset.py --reseed` (what `make demo-reset` runs) now takes
  noticeably longer in wall-clock terms than either number above suggests,
  because it walks the live pipeline for the 7 BACKGROUND cases one at a
  time, sequentially, each with its own inject + per-front approve/poll --
  budget several minutes for the reset step alone, not seconds. That is
  pre-recording setup time, not part of the timed on-camera segment.

## The beat sheet

`fixtures/demo_run.py` prints a `>>> WATCH:` cue at each beat below, in the
same terminal, as it happens -- keep that terminal visible (or on a second
monitor) during the take so the operator doesn't have to memorize this
table. Budget lines are the hard ceilings `demo_run.py` itself enforces
(`ANALYSIS_TIMEOUT_S=180s`, `FILING_TIMEOUT_S=60s` per approved front,
`BUDGET_S=240s` total) -- not narration targets. **Re-time your own dry run
before recording**; the "roughly when" column is a planning aid, not a
promise.

| Beat | What's happening | What the operator watches | Roughly when | If this beat runs long |
|---|---|---|---|---|
| 1. Inject | `POST /demo/inject_bill` blocks while Reader (Gemma pass, then Gemini extraction), Lookup, Clock, Auditor, and Strategist all run for real | Cut to the **live activity feed** the instant the run starts -- this is the single longest beat and the one with the most events to watch fill in | 0:00 -> ~0:45-1:30 | Narrate what the activity feed is doing while it fills (don't just sit in silence) -- `ANALYSIS_TIMEOUT_S=180s` is the hard ceiling before the script itself calls it BLOCKED; if you hit that live, stop, don't wait it out on camera (see "If something breaks" below) |
| 2. Analysis complete | The response returns; case is already `strategy_ready` with fronts, deadlines, and real audit findings | Cut to the **case detail page** for the case id printed by the script; freeze-frame on one citation chip and narrate it out loud | near-instant once beat 1 ends | This beat is a UI cut, not a wait -- if the dashboard is slow to reflect the new case, refresh once rather than staring at a stale page |
| 3. Approve | One `POST /cases/{id}/approve_filing` per applicable front (charity_care + ppdr + audit for the flagship fixture) -- Verifier runs synchronously, then the call returns | Click **Approve** on camera as each is logged -- this is the scored human-in-the-loop gate; don't pre-click it | right after beat 2; the click itself now resolves in low single-digit seconds | If Verifier blocks (409, a real pre-filing issue), say so out loud -- it's the rubric-rewarded human-in-the-loop check working, not a bug; move to the next front rather than debugging it live |
| 4. Filing | Filer runs ASYNCHRONOUSLY off the push subscription; a real PDF is rendered and sent (test mode) | Show the **filing proof** (vendor id / fax-mail confirmation) on the case detail page once status flips to `filed` -- there is now a short visible gap between the Approve click (beat 3) and this | seconds up to ~1 min per front; budget ceiling 60s/front | If a front is still `filing` after ~30s, keep narrating (e.g. explain the async push path) rather than refreshing repeatedly on camera; it either lands within the 60s ceiling or the script itself reports BLOCKED |
| 5. Stats | `GET /dashboard/stats` | Cut to the **stats banner** and let it visibly tick up (open_cases, filings_sent, audit_findings_cents) -- don't cut away before it updates | last few seconds | -- |
| 6. Deployment proof | -- | Cut to the **Cloud Run console** + the public URL bar (MEGAPHONE §4 persona 8 WO3) | after beat 5, before ending the take | -- |

## WO8 task 4: which case to feature, and in what order

The live-INJECTED flagship for beats 1-5 above stays
`case_01_uninsured_gfe_ca` (-> `ef-2026-0001`) -- this is a deliberate
reliability choice, not an oversight: it is uninsured + GFE + California
(PPDR + charity-care free tier, CA's no-deadline safe harbor), so nothing
about it should legitimately block a filing on an unedited take. The other
three candidates below are all real, all strong, but each carries a
narrative or reliability reason to use it as a SECONDARY beat instead:

- **`ef-2026-0007`** (the "four clocks" case) has the richest audit trail in
  the corpus -- five real cash-price-delta findings against Advocate's own
  attested MRF, including the $140-billed-vs-$70-cash line, plus four
  concurrent statutory deadlines on different clocks. This is the single
  best "Architectural Discipline" beat in the whole corpus (30% of the
  score) -- but it is dense, and freeze-framing four different citations
  well needs its own few seconds each. **Recommendation:** use it as the
  architecture-diagram-adjacent beat (MEGAPHONE's 2:40-3:20 block) or as a
  quick pre-seeded cutaway right after beat 2 above ("here's a case with
  four clocks running at once") rather than trying to narrate all four
  clocks inside the live-run block itself, which is timed against ONE
  case's happy path.
- **`ef-2026-0005`** (cat photo) is the most memorable single beat in the
  corpus and needs zero live interaction -- it's already sitting at
  `strategy_ready` with charity_care blocked from `demo-reset`'s reseed, so
  it's a pure cutaway: open the case, show the Verifier's block reason
  naming the specific document, done in under 15 seconds. **Recommendation:**
  this is the single best ~15-second secondary beat to slot in right after
  beat 3 (the live approval gate) -- it's the same human-in-the-loop feature
  the live run just demonstrated succeeding, shown here catching something
  real, which is a stronger pairing than showing it in isolation.
- **`ef-2026-0002`** (unlawful denial) is a strong citation beat (26 CFR
  1.501(r)-4(b)(3), a hospital demanding documents its own FAP doesn't list)
  but it's a static read, not an interaction -- **recommendation:** use it
  during the 0:25-0:50 "why this needs an agent" narrative block instead of
  the live-run block; it illustrates the problem the product solves better
  than it demonstrates the pipeline working.

**Recommended beat order for the 0:50-2:40 live-run block specifically:**
inject `case_01` live (beat 1-2) -> freeze-frame its citation (beat 2) ->
quick cutaway to `ef-2026-0005`'s Verifier block (~15s, see above) -> back to
`case_01` for the approval gate (beat 3) -> filing proof (beat 4) -> stats
banner (beat 5). This keeps the ONE live, unedited action (case_01's full
run) as the spine, with the cat-photo cutaway as a zero-risk, zero-wait
insert that doesn't compete with the live pipeline's own timing budget.

## Before the day

- [ ] `make demo-reset && make demo-run` succeeds **twice in a row**, each
      run under 4 minutes of watchable action (WO4 acceptance). Time it with
      a stopwatch, not a guess.
- [ ] Run it a third time on the actual recording machine/network -- laptop
      Wi-Fi is not the same as whatever ran the rehearsal.
- [ ] Confirm the demo Gmail account, demo Calendar, Phaxio test mode, and
      Lob test mode are all pointed at the SAME project as `demo-reset`.
- [ ] Confirm `DEMO_FAX_ALLOWLIST` / Lob test destinations only -- never a
      real hospital fax or address (RELAY's guardrail, §4 persona 4).
- [ ] Battery charged, laptop plugged in, "Do Not Disturb" on, close Slack/
      email/notifications.
- [ ] Close any tab/window showing a *different* project's data.

## Immediately before recording

- [ ] `make demo-reset` -- watch it finish clean, no BLOCKED lines. This
      purges AND reseeds the 7 background cases against the live API, so
      budget several minutes, not seconds, before recording.
- [ ] Confirm the live `GET /dashboard/stats` numbers are the ones you
      intend to say out loud on camera (open_cases, hospitals,
      audit_findings_cents, etc.) -- **do arithmetic on screen wrong once
      and the judges catch it every time** (WO5/WO8 task 3). In particular:
      `hospitals` must read **4**, not 5 -- if it reads 5, the case_06
      placeholder-EIN regression (see `tests/test_live_banner_reconciliation.py`)
      is back; do not record until it reads 4.
- [ ] Have `case_01_uninsured_gfe_ca` (the safe happy path) queued as the
      primary, live-injected fixture; `ef-2026-0005` (cat photo) queued as
      the zero-risk cutaway; `ef-2026-0007` and `ef-2026-0002` queued as
      architecture/narrative beats if time allows (see WO8 task 4 above).
- [ ] Dashboard open, dark mode, zoomed to 1080p-friendly size (CANVAS §4.6).
- [ ] Cloud Run console tab open and ready to cut to (README §4 persona 8
      WO3: "visible proof of Google Cloud deployment").

## During the take

- [ ] Start the recording BEFORE running `make demo-run` -- the whole run
      must be visible, unedited (§6 risk register: "video must be
      unedited").
- [ ] Narrate the citation as it appears on screen at least once (a judge
      should be able to freeze-frame on a citation).
- [ ] Let the approval gate happen on camera -- human-in-the-loop is a
      scored feature, not a formality to skip past.
- [ ] Remember beats 3 and 4 are no longer the same instant (see "WO8
      update" above) -- don't narrate past the gap as if nothing is
      happening; say what's happening (async filing) instead.
- [ ] Watch the stats banner tick up in view; do not cut away from it.

## If something breaks on camera

- [ ] Do not edit the video to hide it (§6: "no -- video must be
      unedited"). Stop, run `make demo-reset`, and re-take from the top.
- [ ] If Gmail intake is flaky, fall back to `/demo/inject_bill` as primary
      (§6 risk register) -- this is the intended, pre-agreed fallback, not a
      failure.
- [ ] If a vendor test send fails, RELAY's fax/mail interface is swappable
      (§6) -- switch vendors rather than debugging live on camera.
- [ ] If a specific front's Verifier blocks the live-injected case_01 (it
      shouldn't -- that's exactly what this fixture is chosen to avoid) --
      say so plainly on camera as the human-in-the-loop gate working, move
      to the next front, and flag it for a HANDOFF afterward; don't treat it
      as a failed take.
- [ ] If a front looks "stuck" (still `open` well after its Approve click,
      not even `filing`) -- see the "STOP" notice at the top of this file.
      **Do not click Approve on it again** -- a filing may already have gone
      out for it despite the on-screen status; re-approving risks a genuine
      duplicate filing, not just a duplicate click. Stop the take.

## After the take

- [ ] `make demo-reset` again immediately, so the project is clean for the
      next rehearsal or the real take.
- [ ] Save the raw recording in two places before doing anything else to it.
