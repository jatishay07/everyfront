# `infra/`

**Owner:** ATLAS (persona 1)

setup.sh, deploy.sh, service configs, OAUTH.md. Shell + gcloud only -- no Terraform, no GKE (persona guardrail).

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[ATLAS] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
