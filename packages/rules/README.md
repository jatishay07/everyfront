# `packages/rules/`

**Owner:** STATUTE (persona 3)

Deterministic legal rules engine. Pure functions, every docstring cites a regulation. ZERO LLM calls in this package (agreement 2.1).

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[STATUTE] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
