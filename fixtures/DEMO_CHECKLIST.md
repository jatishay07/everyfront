# Demo recording checklist

PROOF (persona 7), WO4. Paper checklist for the recording session
(BUILD_PLAYBOOK.md §5: rehearsals ×3 on Aug 28, record Aug 29). Print it or
keep it on a second monitor -- it is not meant to be read by the agents.

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

- [ ] `make demo-reset` -- watch it finish clean, no BLOCKED lines.
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
