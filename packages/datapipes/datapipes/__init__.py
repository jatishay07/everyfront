"""IRS/CMS/NCCI/FPL data pipelines. Owner: LEDGER (persona 2).

See docs/SPIKE.md for the data quirks this package was built around, and
each submodule's docstring for its work order and sourcing:

  schedule_h   -- WO1: Schedule H Part V Section B parser + URL repair.
  irs_bulk     -- WO1: bulk XML index + batch download/streaming.
  select       -- WO1: facility aggregation + "select, don't sample" seeding.
  firestore_sink, gcs_sink -- WO1: write hospitals/{ein} + CSV mirror.
  seed         -- WO1: `python -m datapipes.seed` end-to-end CLI.
  crosswalk    -- WO2: EIN<->CCN + nonprofit/ownership.
  ncci         -- WO3: PTP/MUE lookup, <10ms.
  mrf          -- WO5: cms-hpt.txt -> MRF -> cash price.

FPL (WO4) is intentionally NOT duplicated here -- see packages/rules/rules/fpl.py,
already implemented by STATUTE; this package does not own it.
"""
