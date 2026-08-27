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

**The allowlist is the pattern in this file, and nothing else.** There used
to be a second allow-source: `DEMO_FAX_ALLOWLIST` / `DEMO_MAIL_ALLOWLIST`
were unioned in, so an operator could name any destination -- including a
real hospital -- and it would be sent to. That is a hole exactly the size of
the guardrail: a safety control that configuration can widen is a
convention, not a control. Those env vars are still read (both are wired
through `services/intake/scripts/go_live.sh` into Secret Manager, so
ignoring them silently would be its own kind of lie), but every entry must
now ITSELF clear the in-code reserved-fictional pattern. They can name a
specific fictional destination; they can no longer widen the set beyond what
this file already permits, and an entry outside it is a loud refusal of
every send rather than a quietly-honored one.

Net effect, which is the property the tests assert: **no value of any
environment variable, and no combination of credentials, makes a real
hospital fax number or street address reachable.**
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
    """Operator-named fax destinations, each of which must itself be inside
    the in-code fictional range. An entry outside it raises -- see the module
    docstring: configuration may narrow this set, never widen it."""
    raw = os.environ.get("DEMO_FAX_ALLOWLIST", "")
    entries = set()
    for entry in raw.split(","):
        if not entry.strip():
            continue
        normalized = _normalize_fax(entry)
        if not _FICTIONAL_FAX_RE.match(normalized):
            raise UnsafeDestinationError(
                f"DEMO_FAX_ALLOWLIST contains {entry.strip()!r}, which is outside the "
                "NANP 555-01XX reserved-fictional range. This env var may only name "
                "destinations the in-code allowlist already permits -- it cannot be "
                "used to widen the guardrail to a real fax number. Refusing every "
                "send until it is corrected."
            )
        entries.add(normalized)
    return frozenset(entries)


def _env_mail_allowlist() -> frozenset[tuple[str, str, str, str]]:
    """Same rule as `_env_fax_allowlist`: each record's ZIP must be inside the
    reserved-unassigned 000XX block."""
    raw = os.environ.get("DEMO_MAIL_ALLOWLIST", "")
    out = set()
    for record in raw.split(";"):
        record = record.strip()
        if not record:
            continue
        parts = [p.strip().lower() for p in record.split("|")]
        if len(parts) != 4:
            raise UnsafeDestinationError(
                f"DEMO_MAIL_ALLOWLIST entry {record!r} is malformed -- expected "
                "'line1|city|state|zip'. Refusing every send until it is corrected: a "
                "safety control that silently drops what it cannot parse is not a "
                "safety control."
            )
        if not _FICTIONAL_ZIP_RE.match(parts[3]):
            raise UnsafeDestinationError(
                f"DEMO_MAIL_ALLOWLIST entry {record!r} has ZIP {parts[3]!r}, outside "
                "the reserved-unassigned 000XX block. This env var may only name "
                "addresses the in-code allowlist already permits -- it cannot be used "
                "to widen the guardrail to a real street address. Refusing every send "
                "until it is corrected."
            )
        out.add(tuple(parts))
    return frozenset(out)


def assert_fax_destination_allowed(number: str) -> str:
    """Return the normalized E.164 number if allowed; raise otherwise."""
    # Validated FIRST, not short-circuited past: a DEMO_FAX_ALLOWLIST holding a
    # real number must refuse EVERY send, including ones to a safe destination,
    # because the misconfiguration is the finding.
    env_allowed = _env_fax_allowlist()
    normalized = _normalize_fax(number)
    if _FICTIONAL_FAX_RE.match(normalized) or normalized in env_allowed:
        return normalized
    raise UnsafeDestinationError(
        f"fax destination {number!r} is not on the in-code allowlist (NANP 555-01XX "
        "fictional range) -- refusing to send. This guardrail exists specifically to "
        "stop a real hospital fax number from being dialed, and no environment "
        "variable can widen it."
    )


def assert_mail_destination_allowed(address: dict) -> dict:
    """`address` is {line1, city, state, zip}. Returns it unchanged if allowed."""
    env_allowed = _env_mail_allowlist()  # validated first -- see the fax twin above
    zip_code = str(address.get("zip", ""))
    key = (
        str(address.get("line1", "")).strip().lower(),
        str(address.get("city", "")).strip().lower(),
        str(address.get("state", "")).strip().lower(),
        zip_code.strip().lower(),
    )
    if _FICTIONAL_ZIP_RE.match(zip_code) or key in env_allowed:
        return address
    raise UnsafeDestinationError(
        f"mail destination {address!r} is not on the in-code allowlist (ZIP 000XX "
        "reserved-unassigned range) -- refusing to send. This guardrail exists "
        "specifically to stop a real hospital address from being mailed, and no "
        "environment variable can widen it."
    )
