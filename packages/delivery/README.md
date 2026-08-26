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

**Re-checked this work order (WO8, 2026-08-26): still true.** Both vendors'
public docs confirm test keys remain free and card-free (Phaxio: "an API
test key ... free, and unlimited API calls"; Lob: a test-prefixed key is
issued the moment you verify your email, no payment method until you
request live keys). What's still missing is the same irreducible human
step as `infra/OAUTH.md`'s Google consent flow: an actual account signup
(email verification, ToS acceptance) under a real identity, which this
session does not have standing to do on anyone's behalf without being asked.
**Found and fixed while re-verifying this, though:** `send_filing`
(`delivery/vendors/filing.py`) never actually set the `"simulated"` key on
the dict it returns -- `agent_core.agents.filer` (services/agent-core) has
read `vendor_result.get("simulated")` since persona 5 WO6 to decide
`filings/{filing_id}.simulated` and "SIMULATED" vs "live" in the audit-trail
narration, but that key was always absent, so it was always `False` --
every single filing, fake vendor included, was being reported as **live**.
Fixed: `"simulated": result.vendor == "fake"` now rides in that same dict.
See this PR's description for the full write-up.

## Calendar + Drive: built and tested, not yet called (WO8)

`delivery/calendar_sync.py` (WO5: every `Deadline` -> the demo Google
Calendar, red if due within 7 days, citation in the description) and
`delivery/drive_sync.py` (WO6: each case's generated filings -> a per-case
Drive folder) are both real, both fully unit-tested (including the
`google_auth.MissingCredentialsError` -> clean `[]`/`None` no-op path), and
both exported from `delivery/__init__.py` for exactly one caller to pick up:
SWARM's Filer/pipeline in `services/agent-core`. Nothing there calls them
yet -- confirmed by grepping that whole service for `calendar_sync`/
`drive_sync`/`sync_deadlines`/`mirror_case_filings`: zero hits outside this
package. `services/agent-core` is SWARM's owned directory (BUILD_PLAYBOOK.md
§0.2), so rather than edit it directly, this PR's description carries a
precise, ready-to-apply HANDOFF: the exact functions, call sites, and the
one `requirements.txt` line agent-core's own Dockerfile is missing
(`google-api-python-client` -- without it, `googleapiclient.discovery.build`
inside both modules' lazy imports raises `ModuleNotFoundError` the moment
credentials ARE configured, the same class of bug this repo's git history
already hit once for `pypdf`/`reportlab` in this same file).

---

Rules of engagement (BUILD_PLAYBOOK.md §0):

- Do **not** modify files outside this directory. Need a change elsewhere? Put a
  `HANDOFF:` note in your PR description for FORGE.
- Cross-agent communication goes through the contracts in §3. If a contract is
  wrong, propose a change -- do not silently diverge.
- Commit messages: `[RELAY] what: why`
- Blocked >30 min? Write a `BLOCKED:` note. Do not invent a workaround that
  violates a contract.
