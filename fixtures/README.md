# `fixtures/`

**Owner:** PROOF (persona 7)

Synthetic patient corpus. ALL fake, watermarked 'SYNTHETIC -- DEMO'. Never a real name, SSN, or real patient bill (rule 0.6).

## Layout

- `cases_data.py` -- the ONE source of truth: 8 `CaseFixture` + 4 `Hospital`
  records (Python dataclasses, not hand-typed JSON). Real hospital name/EIN
  pairs are verified against `docs/SPIKE.md`; every field says how.
- `reference_model.py` -- provisional stand-ins for STATUTE's not-yet-built
  `select_fronts` / `audit_line_items` / `check_denial_lawfulness` (contract
  §3.5), used only so the corpus has *some* testable oracle for those three
  today. See its docstring for the HANDOFF to STATUTE.
- `build.py` -- assembles `cases_data.py` into the contract §3.1 `case.json` /
  `hospitals.json` shapes, cross-checked against the REAL `packages/rules`
  (`compute_deadlines`, `screen_eligibility`) and against
  `reference_model.py` for the rest. Deliberately has NO reportlab/PIL
  dependency, so `tests/test_fixture_corpus.py` and
  `tests/test_stats_consistency.py` run in any environment already running
  the rest of the suite -- including today's CI, before FORGE's HANDOFF below
  is even applied.
- `generate.py` -- imports `build.py` and adds the reportlab/PIL rendering:
  bills, GFEs, denial letters, a collection notice, a pay stub, a cat photo,
  one deliberately corrupted PDF, plus the derived `expected_stats.json`
  (contract §3.4). Nothing under `generated/` is hand-edited -- run
  `.venv/bin/python fixtures/generate.py` after any change to
  `cases_data.py`; `tests/test_fixture_corpus.py` fails if they drift.
- `demo_reset.py` / `demo_run.py` / `Makefile` / `DEMO_CHECKLIST.md` -- the
  demo rehearsal harness (WO4). `make demo-reset && make demo-run` (from
  inside `fixtures/`, or wired into the root Makefile -- see this
  directory's `Makefile` header for the HANDOFF) should produce the full
  happy path twice in a row, under 4 minutes of watchable action.
- `requirements.txt` -- reportlab/pypdf/Pillow (needed only for `generate.py`
  itself and `tests/test_bill_pdfs.py`) + google-cloud-firestore/httpx (the
  nightly e2e suite only). HANDOFF -> FORGE: wire
  `pip install -r fixtures/requirements.txt` into `.github/workflows/ci.yml`
  so `test_bill_pdfs.py` actually runs there instead of skipping.

To regenerate everything from scratch:

```
.venv/bin/python fixtures/generate.py
```

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[PROOF] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
