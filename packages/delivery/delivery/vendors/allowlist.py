"""Hard guardrail (§4 persona 4, RELAY acceptance criteria):

    "never send to a real hospital fax number or address. Enforce an
    allowlist IN CODE, not by convention."

Both vendors are used with test credentials (Phaxio test keys never place a
call; Lob `test_` keys never enter the mail stream), which is the vendor-side
safety net. This module is the SECOND, independent net: even if a key were
ever swapped to a live one by mistake, `send()` in `fax.py`/`mail.py` refuses
to build the request unless the destination clears this check. Convention
("we only use test keys") is exactly the thing agreement item 6 in the risk
register says not to rely on alone.

Two allow-sources, unioned:

  1. A NANP/USPS reserved-fictional pattern, baked into this file, that no
     real hospital destination can ever match by accident.
  2. An explicit operator-provided allowlist (env vars documented in the
     PR's HANDOFF section, since `.env.example` is FORGE's file): comma-
     separated E.164 numbers for fax, and `|`-delimited "line1|city|state|zip"
     records for mail.
"""

from __future__ import annotations

import os
import re

# NANP reserves the 555-0100..555-0199 range in EVERY area code for fictional
# use (film/TV/directory listings) -- carriers do not route real traffic to
# it. A dispute filing can never legitimately need to fax anywhere else in
# test mode, so this is the code-level floor, not a suggestion.
_FICTIONAL_FAX_RE = re.compile(r"^\+1\d{3}55501\d{2}$")

# No NANP-style reserved block exists for postal addresses. The code-level
# floor for mail is therefore a ZIP prefix (00000-00099) that USPS has never
# assigned to a real delivery point, so a real hospital address cannot
# satisfy it by coincidence.
_FICTIONAL_ZIP_RE = re.compile(r"^000\d{2}(-\d{4})?$")


class UnsafeDestinationError(Exception):
    """Raised when a fax/mail destination fails the in-code allowlist check."""


def _normalize_fax(number: str) -> str:
    digits = re.sub(r"[^\d+]", "", number or "")
    if digits.startswith("+"):
        return digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return digits


def _env_fax_allowlist() -> frozenset[str]:
    raw = os.environ.get("DEMO_FAX_ALLOWLIST", "")
    return frozenset(_normalize_fax(n) for n in raw.split(",") if n.strip())


def _env_mail_allowlist() -> frozenset[tuple[str, str, str, str]]:
    raw = os.environ.get("DEMO_MAIL_ALLOWLIST", "")
    out = set()
    for record in raw.split(";"):
        record = record.strip()
        if not record:
            continue
        parts = [p.strip().lower() for p in record.split("|")]
        if len(parts) == 4:
            out.add(tuple(parts))
    return frozenset(out)


def assert_fax_destination_allowed(number: str) -> str:
    """Return the normalized E.164 number if allowed; raise otherwise."""
    normalized = _normalize_fax(number)
    if _FICTIONAL_FAX_RE.match(normalized) or normalized in _env_fax_allowlist():
        return normalized
    raise UnsafeDestinationError(
        f"fax destination {number!r} is not on the in-code allowlist (NANP 555-01XX "
        "fictional range or DEMO_FAX_ALLOWLIST) -- refusing to send. This guardrail "
        "exists specifically to stop a real hospital fax number from being dialed."
    )


def assert_mail_destination_allowed(address: dict) -> dict:
    """`address` is {line1, city, state, zip}. Returns it unchanged if allowed."""
    zip_code = str(address.get("zip", ""))
    key = (
        str(address.get("line1", "")).strip().lower(),
        str(address.get("city", "")).strip().lower(),
        str(address.get("state", "")).strip().lower(),
        zip_code.strip().lower(),
    )
    if _FICTIONAL_ZIP_RE.match(zip_code) or key in _env_mail_allowlist():
        return address
    raise UnsafeDestinationError(
        f"mail destination {address!r} is not on the in-code allowlist (ZIP 000XX "
        "reserved-unassigned range or DEMO_MAIL_ALLOWLIST) -- refusing to send. This "
        "guardrail exists specifically to stop a real hospital address from being mailed."
    )
