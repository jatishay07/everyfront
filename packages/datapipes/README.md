# `packages/datapipes/`

**Owner:** LEDGER (persona 2)

IRS Schedule H / CMS / NCCI / FPL pipelines. See docs/SPIKE.md for the data quirks already found.

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[LEDGER] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
