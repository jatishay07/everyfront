# `packages/delivery/`

**Owner:** RELAY (persona 4)

Phaxio (fax), Lob (certified mail), PDF fill, Calendar, Drive. Vendors behind a swappable interface.

---

## Vendor credentials (WO7)

**Status as of this work order: no live Phaxio or Lob credentials have been
obtained.** Both are behind a signup flow that requires a human (email
verification, and Phaxio's test keys are issued from an account dashboard
after signup) -- not something an agent session can complete on its own.
Both are free to obtain, though:

- **Phaxio** (`www.phaxio.com`): free account, test-mode API key + secret
  issued immediately from the dashboard under **API Keys** -- no card
  required, no verification wait, sends never leave test mode until you
  explicitly request live keys.
- **Lob** (`www.lob.com`): free account, `test_...`-prefixed API key issued
  immediately from **Settings > API Keys** -- same story, test keys never
  enter the physical mail stream.

**What changes the moment real test keys exist:** nothing in the code.
`PhaxioFaxClient`/`LobMailClient` (`delivery/vendors/fax.py`,
`delivery/vendors/mail.py`) already read `PHAXIO_API_KEY`/`PHAXIO_API_SECRET`
/`LOB_API_KEY` from the environment and only fall back to the recording
`FakeFaxVendor`/`FakeMailVendor` when those are unset (or on a request
exception). Run `services/intake/scripts/go_live.sh` with
`PHAXIO_API_KEY`/`PHAXIO_API_SECRET`/`LOB_API_KEY` set once you have them --
it creates the Secret Manager secrets and wires them into `ef-agent-core`
(see that script's header comment). No redeploy, no code change.

**Until then**, every send goes through `FakeFaxVendor`/`FakeMailVendor`
(`delivery/vendors/fake.py`). Their result is never presented as a real
vendor send: `VendorResult.vendor == "fake"` (not `"phaxio"`/`"lob"`) rides
all the way through `filing.py`'s `send_filing` into `filings/{filing_id}`,
and the proof object is shaped exactly like a real one -- a `phaxio_id`/
`lob_id` plus, for mail, a USPS-format tracking number -- specifically so a
judge or the dashboard can inspect what "sent" produced without the record
lying about whether it was real.

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[RELAY] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
