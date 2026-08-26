# PROOF (persona 7) -- demo rehearsal harness.
#
# BUILD_PLAYBOOK.md §1.5 puts the repo-root Makefile under FORGE's ownership,
# and PROOF owns only fixtures/ and tests/ (§0.2: never touch another
# persona's directory). So these targets live here; see this PR's
# description for the HANDOFF text to paste into the root Makefile, e.g.:
#
#   demo-reset:
#   	$(MAKE) -C fixtures demo-reset
#   demo-run:
#   	$(MAKE) -C fixtures demo-run
#
# or simply `include fixtures/Makefile` from the root Makefile.
#
# Acceptance (§4 persona 7 WO4): `make demo-reset && make demo-run` produces
# the full happy path TWICE IN A ROW, in under 4 minutes of watchable action.
# `demo-cycle` below runs exactly that sequence.

PYTHON ?= .venv/bin/python

.PHONY: demo-reset demo-reset-purge-only demo-run demo-cycle demo-reset-dry demo-run-dry

# --reseed: purge, then reseed the 7 background cases (contract-shaped,
# human-plausible ids -- see fixtures/demo_reset.py's docstring); the 8th
# case is left for `demo-run` to inject live, on camera.
demo-reset:
	$(PYTHON) fixtures/demo_reset.py --reseed

# Purge-only, no reseed -- for a fast cleanup between ad hoc test injections
# when a full pre-recording reseed isn't needed.
demo-reset-purge-only:
	$(PYTHON) fixtures/demo_reset.py

demo-run:
	$(PYTHON) fixtures/demo_run.py

# Offline sanity checks -- no GCP credentials or live deployment required.
# What CI actually exercises (see tests/test_demo_harness.py); use these to
# rehearse the script's own logic/timing narration before a real recording.
demo-reset-dry:
	$(PYTHON) fixtures/demo_reset.py --dry-run --reseed

demo-run-dry:
	$(PYTHON) fixtures/demo_run.py --dry-run

# The WO4 acceptance test, verbatim: reset, run, reset, run.
demo-cycle: demo-reset demo-run demo-reset demo-run
