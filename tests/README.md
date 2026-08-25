# `tests/`

**Owner:** PROOF (persona 7)

Unit + e2e. E2E asserts Firestore end-state after inject.

## What's here

- `test_fpl.py`, `test_deadlines.py`, `test_eligibility.py`,
  `test_contracts.py` -- STATUTE's + FORGE's, pre-existing, do not break.
- `test_fixture_corpus.py` -- validates `fixtures/cases_data.py` +
  `fixtures/generated/` against contract §3.1, the §2.6 CA/IL state rule, the
  watermark rule, and the REAL `packages/rules` functions (deadlines +
  eligibility). Only imports `fixtures/build.py` (no reportlab dependency),
  so it runs in today's CI as-is.
- `test_bill_pdfs.py` -- validates the rendered PDFs/PNG: parseable,
  watermarked every page, FAP notice (or the honest for-profit line) present,
  seeded duplicate/NCCI-style/MUE-style findings extractable, the corrupted
  fixture fails to parse. Needs reportlab/pypdf/Pillow
  (`pytest.importorskip`s itself out if absent -- see `fixtures/requirements.txt`).
- `test_stats_consistency.py` -- WO5's "the §3.4 numbers must add up",
  pulled forward and enforced now: recomputes the whole stat object from
  scratch off the committed fixtures and diffs it against the committed
  `expected_stats.json`.
- `test_demo_harness.py` -- offline (`--dry-run`) checks of
  `fixtures/demo_reset.py` / `demo_run.py` / `Makefile`.
- `test_e2e_happy_path.py` -- `@pytest.mark.e2e`, skipped by default
  (`pytest -m "not e2e"`); needs a live staging `services/api` + Firestore,
  neither of which exists yet (SWARM WO1-4). Skips cleanly with a clear
  reason until then.
- `conftest.py` -- puts the repo root on `sys.path` so `import fixtures...`
  resolves from any test module.

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[PROOF] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
