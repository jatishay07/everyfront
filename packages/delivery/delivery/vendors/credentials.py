"""The second hard guardrail: this system may only ever hold the phone with
a vendor in TEST MODE.

`allowlist.py` stops a real destination. This module stops a real *credential*
-- and it runs FIRST, before the destination is even looked at, so that no
combination of a production key with any destination (including one an
operator put in `DEMO_FAX_ALLOWLIST`) can reach `requests.post`.

Why both nets, when either would do: this system drafts legal correspondence
about someone's medical bills. A misdirected fax is a stranger receiving a
patient's health information; a misdirected certified letter is the same
thing, physical and unrecallable. §4 persona 4's guardrail ("never send to a
real hospital fax/address -- test destinations only; hard allowlist in code")
is treated here as the acceptance criterion, not a caveat.

WHAT A TEST CREDENTIAL LOOKS LIKE, per vendor, with sources
-----------------------------------------------------------
**Lob** publishes the distinction in the key itself. docs.lob.com, "API Keys":
there are two environments and the keys are prefixed -- test keys are
`test_...`, live keys are `live_...`. So the check is exact and needs no
operator input: anything not starting with `test_` is refused.

**Phaxio does not.** Phaxio's own docs
(https://www.phaxio.com/docs/api/v2/ and
https://www.phaxio.com/blog/guide/test-credentials) describe live vs test
credentials only *functionally* -- "If you make calls with test keys, the
Phaxio system will simulate faxes being sent or received and your balance
will not be affected" -- and state they are issued from the API Settings
page. Neither page documents any prefix or shape that distinguishes the two.
Fetched and re-read while writing this module; if a future Phaxio key format
does carry a marker, `_PHAXIO_TEST_MARKER_RE` is where it goes.

Because the vendor gives us no signal, this module **refuses by default** and
requires one of two positive signals:

  * the key or secret itself begins with `test` (`test_`/`test-`), or
  * `PHAXIO_API_MODE` is set to exactly `test`.

Silence is refusal. An unset `PHAXIO_API_MODE` with a key that carries no
marker is a refusal, not a best-effort send -- which is the correct failure
mode when the question is "is this key going to dial a real phone number?"
and the honest answer is "we cannot tell."
"""

from __future__ import annotations

import os
import re

LOB_TEST_KEY_PREFIX = "test_"
LOB_LIVE_KEY_PREFIX = "live_"

# Case-insensitive; covers `test_...` and `test-...`.
_PHAXIO_TEST_MARKER_RE = re.compile(r"^test[_-]", re.IGNORECASE)

PHAXIO_MODE_ENV = "PHAXIO_API_MODE"


class ProductionCredentialError(Exception):
    """Raised when a vendor credential is not provably a TEST credential.

    Deliberately NOT caught by the vendor clients' degrade-to-stub handler:
    a network blip is a reason to fall back to a simulated send, a production
    API key sitting in this system's environment is a reason to stop and tell
    a human.
    """


def assert_phaxio_credentials_are_test(api_key: str, api_secret: str) -> None:
    """Refuse unless the Phaxio credential is provably a test credential.

    Fails closed: with no positive signal this raises. See the module
    docstring for why Phaxio needs `PHAXIO_API_MODE` and Lob does not.
    """
    if _PHAXIO_TEST_MARKER_RE.match(api_key or "") or _PHAXIO_TEST_MARKER_RE.match(
        api_secret or ""
    ):
        return
    if os.environ.get(PHAXIO_MODE_ENV, "").strip().lower() == "test":
        return
    raise ProductionCredentialError(
        "refusing to call Phaxio: this credential is not provably a TEST credential. "
        "Phaxio does not document a prefix that distinguishes test keys from live "
        "keys (see https://www.phaxio.com/docs/api/v2/), so this system will not "
        "guess -- a wrong guess dials a real phone number carrying a patient's "
        f"health information. Either use a key/secret beginning with 'test_', or set "
        f"{PHAXIO_MODE_ENV}=test on the service to attest that the configured "
        "credential is the test pair from Phaxio's API Settings page. Until then "
        "every fax stays a labelled simulation."
    )


def assert_lob_key_is_test(api_key: str) -> None:
    """Refuse unless the Lob key carries Lob's documented `test_` prefix.

    docs.lob.com: keys come as a test/live pair, formatted `test_*` and
    `live_*`. A `live_` key prints and mails a physical letter.
    """
    key = api_key or ""
    if key.startswith(LOB_TEST_KEY_PREFIX):
        return
    shape = (
        "a LIVE key -- it would print and mail a physical certified letter"
        if key.startswith(LOB_LIVE_KEY_PREFIX)
        else "not in Lob's documented test-key form"
    )
    raise ProductionCredentialError(
        f"refusing to call Lob: the configured LOB_API_KEY is {shape}. Lob prefixes "
        f"test keys '{LOB_TEST_KEY_PREFIX}' and live keys '{LOB_LIVE_KEY_PREFIX}' "
        "(docs.lob.com, API Keys); only a test key may ever be configured here, "
        "because a certified letter to the wrong address is physical and "
        "irreversible. Until a test key is configured every letter stays a "
        "labelled simulation."
    )
