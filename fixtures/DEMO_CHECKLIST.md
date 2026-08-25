# Demo recording checklist

PROOF (persona 7), WO4 + WO6 task 3. Paper checklist for the recording
session (BUILD_PLAYBOOK.md §5: rehearsals ×3 on Aug 28, record Aug 29). Print
it or keep it on a second monitor -- it is not meant to be read by the agents.

## The beat sheet (WO6 task 3: "time each beat and write down what the
operator should be looking at when")

`fixtures/demo_run.py` now prints a `>>> WATCH:` cue at each beat below, in
the same terminal, as it happens -- keep that terminal visible (or on a
second monitor) during the take so the operator doesn't have to memorize
this table. Timings are the last live observations recorded in this repo
(`demo_run.py`'s own docstring: ~130s for the inject call against a
3-document fixture on 2026-08-25; two full-run totals of 75.7s and 69.6s were
also logged against an earlier, lighter version of this fixture). **Re-time
your own dry run before recording** -- the corpus gained a real `line_items`
list and the Filer started rendering real PDFs since those numbers were
taken, so the true number today is very likely higher, not lower. Budget
lines below are the hard ceilings `demo_run.py` itself enforces
(`ANALYSIS_TIMEOUT_S=180s`, `FILING_TIMEOUT_S=60s` per approved front,
`BUDGET_S=240s` total) -- not narration targets.

| Beat | What's happening | What the operator watches | Roughly when |
|---|---|---|---|
| 1. Inject | `POST /demo/inject_bill` blocks while Reader (Gemma pass, then Gemini extraction), Lookup, Clock, Auditor, and Strategist all run for real | Cut to the **live activity feed** the instant the run starts -- this is the single longest beat and the one with the most events to watch fill in | 0:00 -> ~1:30-2:00 (last observed ~130s; budget ceiling 180s) |
| 2. Analysis complete | The response returns; case is already `strategy_ready` with fronts, deadlines, and (since the corpus's WO6 rewire) real audit findings | Cut to the **case detail page** for the case id printed by the script; freeze-frame on one citation chip and narrate it out loud (CANVAS §4 WO2) | ~1:30-2:00, near-instant once beat 1 ends |
| 3. Approve | One `POST /cases/{id}/approve_filing` per applicable front (charity_care + ppdr + audit for the flagship fixture) | Click **Approve** on camera as each is logged -- this is the scored human-in-the-loop gate; don't pre-click it | right after beat 2, a few seconds per click |
| 4. Filing | Strategist/Verifier/Filer run per approved front; a real PDF is rendered and sent (test mode) | Show the **filing proof** (vendor id / fax-mail confirmation) on the case detail page for a beat | seconds to ~1 min per front; budget ceiling 60s/front |
| 5. Stats | `GET /dashboard/stats` | Cut to the **stats banner** and let it visibly tick up (open_cases, filings_sent, audit_findings_cents) -- don't cut away before it updates | last few seconds |
| 6. Deployment proof | -- | Cut to the **Cloud Run console** + the public URL bar (MEGAPHONE §4 persona 8 WO3) | after beat 5, before ending the take |

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

- [ ] `make demo-reset` -- watch it finish clean, no BLOCKED lines. This now
      purges AND reseeds the 7 background cases (WO6 task 1) against the
      live API, so budget several minutes, not seconds, before recording.
- [ ] Confirm `fixtures/generated/expected_stats.json` numbers are the ones
      you intend to say out loud on camera (open_cases, hospitals,
      audit_findings_cents, etc.) -- **do arithmetic on screen wrong once and
      the judges catch it every time** (WO5).
- [ ] Have `case_01_uninsured_gfe_ca` (the safe happy path) queued as the
      primary fixture; `case_02_wrongful_denial_il` and
      `case_07_il_concurrent_clocks` queued as the deadline-drama beats if
      time allows.
- [ ] Dashboard open, dark mode, zoomed to 1080p-friendly size (CANVAS §4.6).
- [ ] Cloud Run console tab open and ready to cut to (README §4 persona 8
      WO3: "visible proof of Google Cloud deployment").

## During the take

- [ ] Start the recording BEFORE running `make demo-run` -- the whole run
      must be visible, unedited (§6 risk register: "video must be
      unedited").
- [ ] Narrate the citation as it appears on screen at least once (a judge
      should be able to freeze-frame on a citation, per CANVAS §4 WO2).
- [ ] Let the approval gate happen on camera -- human-in-the-loop is a scored
      feature, not a formality to skip past.
- [ ] Watch the stats banner tick up in view; do not cut away from it.

## If something breaks on camera

- [ ] Do not edit the video to hide it (§6: "no -- video must be
      unedited"). Stop, run `make demo-reset`, and re-take from the top.
- [ ] If Gmail intake is flaky, fall back to `/demo/inject_bill` as primary
      (§6 risk register) -- this is the intended, pre-agreed fallback, not a
      failure.
- [ ] If a vendor test send fails, RELAY's fax/mail interface is swappable
      (§6) -- switch vendors rather than debugging live on camera.

## After the take

- [ ] `make demo-reset` again immediately, so the project is clean for the
      next rehearsal or the real take.
- [ ] Save the raw recording in two places before doing anything else to it.
