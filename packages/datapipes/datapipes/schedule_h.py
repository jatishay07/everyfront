"""IRS Form 990 Schedule H, Part V Section B parser.

Promoted from `docs/spike/parse_schedule_h.py` (FORGE day-1 spike, gate (a),
PASS -- see `docs/SPIKE.md`). This is LEDGER work order 1, the "crown jewel"
pipeline: recover a hospital's Financial Assistance Policy facts straight from
the IRS e-file XML, for `hospitals/{ein}` (contract §3.1).

Source: IRS Form 990 Schedule H, Part V Section B ("Facility Policies and
Practices"), required of hospital organizations under 26 CFR 1.501(r)-4.
Schema namespace http://www.irs.gov/efile; confirmed stable across return
versions 2021v4.0 through 2023v5.1 (docs/SPIKE.md gate (a)).

Fields extracted, per hospital FACILITY (a single 990 return covers many
facilities under one EIN):
  line 13a  FPGFamilyIncmLmtFreeCarePct      -> free_care_max_fpl_pct
  line 13a  FPGFamilyIncmLmtDscntCarePct     -> discounted_care_max_fpl_pct
  line 16a  FAPAvailableOnWebsiteURLTxt      -> fap_url
  line 16b  FAPAppAvailableOnWebsiteURLTxt   -> fap_app_url
  line 16c  FAPSummaryOnWebsiteURLTxt        -> fap_summary_url

Plus org-level identity, read from ReturnHeader/Filer (NOT a direct child of
ReturnHeader -- see the EIN comment in `parse_return`):
  EIN, org name, state, phone.

DATA QUIRKS (docs/SPIKE.md gate (a) -- all handled here):

1. THE ZERO SENTINEL. A threshold of literal ``0`` means the tier is NOT
   OFFERED, not "0% of FPL". Sutter Bay reports this on all 7 facilities for
   discounted care. This module normalizes 0 -> None so a naive downstream
   comparison can't silently deny everyone. (STATUTE's `eligibility.py` also
   guards this independently -- defense in depth on a correctness trap.)
2. 66.6% of line 16a values are not URLs at all -- they're cross-references
   ("SEE PART V, SECTION C"). `classify_url` flags these rather than trying
   to repair them into something they aren't.
3. A repair pass recovers real URLs that are missing a scheme
   (``WWW.SENTARA.COM/...``) or missing the colon (``HTTPS//WVUMEDICINE.ORG``).
   Measured lift: 31.8% -> 61.2% usable. `repair_url` implements this.
4. 62% of usable URLs are upper-cased. Scheme and host are lower-cased on
   repair; paths are left alone because they can be case-sensitive.
5. `FacilityNum` is missing on ~16% of rows and rows are unordered -- do not
   assume row order corresponds to facility number.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

NS = {"efile": "http://www.irs.gov/efile"}
NST = "{http://www.irs.gov/efile}"

# Part V Section B line number -> XML element name.
LINE_13A_FREE = "FPGFamilyIncmLmtFreeCarePct"
LINE_13A_DISCOUNTED = "FPGFamilyIncmLmtDscntCarePct"
LINE_16A_FAP_URL = "FAPAvailableOnWebsiteURLTxt"
LINE_16B_APP_URL = "FAPAppAvailableOnWebsiteURLTxt"
LINE_16C_SUMMARY_URL = "FAPSummaryOnWebsiteURLTxt"

# A threshold reported as exactly 0 means "not offered". See module docstring
# quirk (1). Kept here (not just in rules/eligibility.py) so a CSV/Firestore
# consumer that skips STATUTE's screen never sees a bare, misleading 0.
NOT_OFFERED_SENTINEL = 0

UrlStatus = Literal["usable", "repaired", "cross_reference", "blank", "unrepairable"]

_CROSS_REF_RE = re.compile(r"\bSEE\s+PART\s+V\b|\bSEE\s+SCHEDULE\b|\bSEE\s+PAGE\b", re.I)
_MISSING_COLON_RE = re.compile(r"^(https?)//", re.I)
_HAS_SCHEME_RE = re.compile(r"^https?://", re.I)
_LOOKS_LIKE_HOST_RE = re.compile(r"^(www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(/.*)?$", re.I)


@dataclass
class Facility:
    """One `HospitalFcltyPoliciesPrctcGrp` row -- a single hospital facility."""

    ein: str
    org_name: str | None
    state: str | None
    tax_period_end: str | None
    facility_num: str | None
    name: str | None
    free_care_max_fpl_pct: int | None
    discounted_care_max_fpl_pct: int | None
    fap_url_raw: str | None
    fap_app_url_raw: str | None
    fap_summary_url_raw: str | None
    fap_url: str | None = None
    fap_url_status: UrlStatus = "blank"
    quirks: list[str] = field(default_factory=list)


def _text(node: ET.Element | None, tag: str) -> str | None:
    if node is None:
        return None
    el = node.find(f"efile:{tag}", NS)
    if el is None or el.text is None:
        return None
    val = el.text.strip()
    return val or None


def _pct(node: ET.Element, tag: str, quirks: list[str]) -> int | None:
    """Normalize a Schedule H FPL threshold to int, honoring the zero sentinel.

    Returns None for "not reported", non-numeric junk (flagged, never
    guessed -- LEDGER work order 1), and the zero sentinel (quirk 1).
    """
    raw = _text(node, tag)
    if raw is None:
        return None
    try:
        val = int(float(raw))
    except ValueError:
        quirks.append(f"{tag}={raw!r} not numeric")
        return None
    if val == NOT_OFFERED_SENTINEL:
        quirks.append(f"{tag}=0 treated as NOT OFFERED, not 0% FPL")
        return None
    if val < 0:
        quirks.append(f"{tag}={val} negative; ignored")
        return None
    return val


def classify_url(raw: str | None) -> tuple[UrlStatus, str | None]:
    """Classify + repair a Schedule H line 16a/16b/16c value.

    Returns (status, repaired_url). `repaired_url` is only set for "usable"
    (no repair needed) and "repaired" (repair pass fixed it); it is always a
    lowercase-scheme, lowercase-host, http(s) URL in those two cases.

    See module docstring quirks 2-4 for the exact patterns handled.
    """
    if raw is None or not raw.strip():
        return "blank", None
    raw = raw.strip()

    if _CROSS_REF_RE.search(raw):
        return "cross_reference", None

    candidate = raw
    repaired = False

    if not _HAS_SCHEME_RE.match(candidate):
        # Missing colon: "HTTPS//HOST/PATH" -> "HTTPS://HOST/PATH"
        m = _MISSING_COLON_RE.match(candidate)
        if m:
            candidate = candidate.replace("//", "://", 1)
            repaired = True
        elif _LOOKS_LIKE_HOST_RE.match(candidate):
            # Missing scheme entirely: "WWW.SENTARA.COM/..." -> add http://
            candidate = "http://" + candidate
            repaired = True
        else:
            return "unrepairable", None

    parts = urlsplit(candidate)
    if not parts.netloc or "." not in parts.netloc:
        return "unrepairable", None

    # Lowercase scheme + host only; paths/query can be case-sensitive.
    normalized = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment)
    )
    return ("repaired" if repaired else "usable"), normalized


@dataclass
class OrgReturn:
    """One IRS 990 filing: org identity + every hospital facility it reports."""

    ein: str
    org_name: str | None
    state: str | None
    tax_period_end: str | None
    facilities: list[Facility]


def parse_return(xml_bytes: bytes, *, source: str = "<bytes>") -> OrgReturn:
    """Parse one 990 e-file XML document (already read into memory).

    Raises ValueError if the filer EIN can't be resolved -- a silent "" would
    corrupt every downstream `hospitals/{ein}` key (spike gate (a) finding).
    """
    root = ET.fromstring(xml_bytes)
    hdr = root.find("efile:ReturnHeader", NS)
    if hdr is None:
        raise ValueError(f"{source}: no ReturnHeader")
    filer = hdr.find("efile:Filer", NS)
    # EIN lives at ReturnHeader/Filer/EIN, not as a direct child of
    # ReturnHeader -- reading the wrong path yields a silent empty string.
    ein = _text(filer, "EIN")
    if not ein:
        raise ValueError(f"{source}: could not resolve filer EIN")

    org_name = None
    if filer is not None:
        name_el = filer.find("efile:BusinessName", NS)
        org_name = _text(name_el, "BusinessNameLine1Txt")
    state = None
    if filer is not None:
        addr = filer.find("efile:USAddress", NS)
        state = _text(addr, "StateAbbreviationCd")
    period = hdr.findtext("efile:TaxPeriodEndDt", default=None, namespaces=NS)

    facilities: list[Facility] = []
    for grp in root.iter(f"{NST}HospitalFcltyPoliciesPrctcGrp"):
        quirks: list[str] = []
        name_el = grp.find("efile:HospitalFacilityName", NS)
        name = _text(name_el, "BusinessNameLine1Txt") if name_el is not None else None

        fap_raw = _text(grp, LINE_16A_FAP_URL)
        fap_app_raw = _text(grp, LINE_16B_APP_URL)
        fap_summary_raw = _text(grp, LINE_16C_SUMMARY_URL)
        status, repaired = classify_url(fap_raw)

        fac = Facility(
            ein=ein,
            org_name=org_name,
            state=state,
            tax_period_end=period,
            facility_num=_text(grp, "FacilityNum"),
            name=name,
            free_care_max_fpl_pct=_pct(grp, LINE_13A_FREE, quirks),
            discounted_care_max_fpl_pct=_pct(grp, LINE_13A_DISCOUNTED, quirks),
            fap_url_raw=fap_raw,
            fap_app_url_raw=fap_app_raw,
            fap_summary_url_raw=fap_summary_raw,
            fap_url=repaired,
            fap_url_status=status,
            quirks=quirks,
        )
        if status == "blank":
            fac.quirks.append("line 16a blank -- no FAP URL published in filing")
        elif status == "cross_reference":
            fac.quirks.append(f"line 16a is a cross-reference, not a URL: {fap_raw!r}")
        elif status == "repaired":
            fac.quirks.append(f"line 16a repaired: {fap_raw!r} -> {repaired!r}")
        elif status == "unrepairable":
            fac.quirks.append(f"line 16a not a recognizable URL: {fap_raw!r}")
        facilities.append(fac)

    return OrgReturn(
        ein=ein, org_name=org_name, state=state, tax_period_end=period, facilities=facilities
    )


def parse(path: str) -> list[Facility]:
    """Parse a 990 XML file on disk. Convenience wrapper (mirrors the spike CLI)."""
    with open(path, "rb") as fh:
        data = fh.read()
    return parse_return(data, source=path).facilities
