# `packages/delivery/`

**Owner:** RELAY (persona 4)

Phaxio (fax), Lob (certified mail), PDF fill, Calendar, Drive. Vendors behind a swappable interface.

---

## Safety posture: test mode is enforced, not assumed

This package drafts and delivers legal correspondence about a patient's
medical bills. A misdirected fax is a stranger receiving someone's health
information; a misdirected certified letter is the same thing, physical and
unrecallable. §4 persona 4's guardrail -- *"never send to a real hospital
fax/address -- test destinations only; hard allowlist in code"* -- is
therefore treated as the acceptance criterion this package is built around.

Two independent gates, both **in code**, both **fail-closed**, and the
credential gate deliberately runs **first**:

**1. The credential gate** (`delivery/vendors/credentials.py`). Every send
refuses unless the configured credential is provably a *test* credential.
This runs before the destination is even normalized, so there is no ordering
in which a production key reaches `requests.post`.

- **Lob** publishes the distinction in the key itself: test keys are
  `test_...`, live keys `live_...` (docs.lob.com, "API Keys"). Exact check,
  no operator input needed. A `live_` key is refused with a message saying
  why.
- **Phaxio does not.** Its docs describe test vs live credentials only
  functionally ("the Phaxio system will simulate faxes being sent or
  received and your balance will not be affected") and never document a
  prefix. So this package refuses to guess: it accepts a Phaxio credential
  only if the key/secret begins with `test_`/`test-`, **or** the operator has
  explicitly set `PHAXIO_API_MODE=test`. Unset is a refusal, not a
  best-effort send.

**2. The destination allowlist** (`delivery/vendors/allowlist.py`). Fax
destinations must be inside the NANP reserved-fictional block
(`555-0100..555-0199`, which carriers do not route); mail destinations must
carry a ZIP in the reserved-unassigned `000XX` block. `DEMO_FAX_ALLOWLIST` /
`DEMO_MAIL_ALLOWLIST` are still read, but **every entry must itself clear
those in-code patterns** -- configuration can name a specific fictional
destination, it can no longer widen the guardrail, and an out-of-range entry
refuses *every* send until a human fixes it. That closed a real hole: those
two env vars are wired from Secret Manager by
`services/intake/scripts/go_live.sh`, and until this change a real hospital
fax number placed in one was simply honored.

The property the test suite asserts directly, in
`tests/test_vendors_live_clients.py::TestNoProductionSendIsReachable`:
**no combination of credentials and environment variables makes a real
hospital fax number or street address reachable.** Those tests assert on
"zero HTTP requests left this process", not merely on an exception type.

## The `simulated` contract (published for `services/agent-core`)

Defined normatively in `delivery/vendors/base.py`'s `VendorResult`
docstring. In brief:

| value | meaning |
|---|---|
| `simulated: True`  | Nothing left this system that could reach a real recipient -- a stub vendor, **or** a real vendor call made under test-mode credentials. |
| `simulated: False` | A genuine production send: production credentials, a fax dialed or a letter printed. |

Three rules the two halves depend on:

1. **Always present, never `None`.** `VendorResult.simulated` is a required
   field with no default, so a new vendor client cannot forget it and inherit
   a silent `False`.
2. **`vendor == "fake"` implies `simulated is True`**, always. `filing.py`
   re-asserts this instead of trusting the client.
3. **Fail closed.** A client that returns no boolean `simulated` is reported
   as `True`. Under-claiming a live send is safe; over-claiming one is how a
   stub gets reported to a patient as a filed dispute.

`send_filing()` copies the value verbatim into its returned dict under the
key `"simulated"` -- the key `agent_core.agents.filer` already reads to set
`filings/{filing_id}.simulated` and to narrate "SIMULATED" vs "live".
**Persisting and surfacing it is agent-core's half**; this package's half is
making the value unambiguous and always present.

**As of this change `simulated` is `True` for every send this package can
produce**, because production credentials are refused outright. `False` is
reachable only by a future client that deliberately opts into production
mode. The `proof` object also carries `mode`: `"stub"` (no vendor call) or
`"test"` (a real vendor call under test credentials).

## Getting the test keys -- the exact human steps

Neither vendor can be signed up for by an agent session: both need email
verification and ToS acceptance under a real identity. Both are free and
neither asks for a card to issue test keys.

### Phaxio (fax)

1. Sign up at **https://www.phaxio.com** (free; no payment method needed for
   test credentials).
2. Verify the confirmation email.
3. In the console, open **API Settings** -- Phaxio's own docs name this page.
   You will see two credential pairs, **live** and **test**. Take the
   **test** pair.
4. Shape: an **API key** and a separate **API secret**, used as HTTP basic
   auth (`-u 'API_KEY:API_SECRET'`). Phaxio publishes no prefix that marks a
   key as test -- if yours does not begin with `test_`, that is normal, and
   step 6 is then mandatory.
5. Env vars, matching `services/intake/scripts/go_live.sh` exactly:
   - `PHAXIO_API_KEY` → Secret Manager secret **`phaxio-api-key`**
   - `PHAXIO_API_SECRET` → Secret Manager secret **`phaxio-api-secret`**
6. **Also set `PHAXIO_API_MODE=test`** on `ef-agent-core` unless the key
   itself starts with `test_`. Without it every fax stays a labelled
   simulation, loudly, by design. `go_live.sh` does not wire this variable
   today -- see the HANDOFF below.

### Lob (certified mail)

1. Sign up at **https://www.lob.com** (free).
2. Verify the confirmation email.
3. Open **Settings → API Keys**. Lob issues a test/live pair per environment;
   take the **secret test key**.
4. Shape: a single key beginning **`test_`** (live keys begin `live_`). Used
   as HTTP basic auth username with an empty password.
5. Env var, matching `go_live.sh` exactly:
   - `LOB_API_KEY` → Secret Manager secret **`lob-api-key`**
   - A `live_` key is refused by this package on principle. Do not configure
     one.

### Wiring them up

```bash
PROJECT_ID=everyfront-hack-2026 \
PHAXIO_API_KEY=... PHAXIO_API_SECRET=... LOB_API_KEY=test_... \
  ./services/intake/scripts/go_live.sh
```

That script creates/updates the three secrets and attaches them to
`ef-agent-core` as env vars. No code change, no redeploy.

> **HANDOFF for FORGE / whoever owns `services/intake/`:**
> `go_live.sh`'s `SECRET_SPECS` and `AGENT_SECRET_MAP` need one more entry so
> an unmarked Phaxio test key can be used at all:
> `"phaxio-api-mode:PHAXIO_API_MODE"` in `SECRET_SPECS`, and
> `"PHAXIO_API_MODE:phaxio-api-mode"` in the `AGENT_SECRET_MAP` loop. It is
> not secret (the value is the literal string `test`), but that script is
> where vendor credential wiring already lives, so keeping it there avoids
> the split-ownership drift its own header warns about. Alternatively set it
> as a plain env var on `ef-agent-core` in `infra/deploy.sh`. Until one of
> those happens, a Phaxio key whose value does not begin with `test_` cannot
> be used and every fax remains a labelled simulation -- which is the safe
> failure, not a silent one.

### What is still unproven

No Phaxio or Lob account exists. Both clients are exercised only against a
faked `requests.post` with request/response bodies copied from the vendors'
published references (`tests/test_vendors_live_clients.py` cites each one).
**Nothing here demonstrates that a real vendor accepts these requests** --
only that the clients send what the documentation describes. That gap closes
the first time a human runs one test key through it, and not before.

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
