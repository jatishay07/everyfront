# `web/`

**Owner:** CANVAS (persona 6)

Next.js 14 + Tailwind dashboard, dark-mode-first. Build against mocked API from contract 3.3 -- do not wait for the real one.

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[CANVAS] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
