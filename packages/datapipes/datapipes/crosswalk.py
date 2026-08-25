"""EIN <-> CCN crosswalk + nonprofit/ownership enrichment.

Work order 2. Per docs/SPIKE.md gate (b): CMS mandates MRF filenames of the
form `<ein>_<hospital-name>_standardcharges.<ext>`, and the day-1 spike
confirmed a real filename (`362169147_advocate-christ-medical-center_...`)
matches Advocate's Schedule H EIN exactly. That closes the EIN<->hospital
identity for free whenever we already have an MRF URL (from `mrf.py`) --
making it the PRIMARY join, per the spike's explicit handoff to LEDGER WO2.

The Community Benefit Insight (CBI) API (`communitybenefitinsight.org`) is
the FALLBACK: a real, live, no-auth API returning EIN + CMS Certification
Number (CCN, called `medicare_provider_number` in its schema) + address per
state, built by RTI by matching Schedule H orgs to CMS Provider-of-Service
data. Verified live 2026-08-25 (`?state=IL` returns real EIN/CCN pairs).

CMS Hospital General Information (data.cms.gov, dataset xubh-q36u) is joined
by CCN to get ownership type -- the field that decides `nonprofit: bool`.
For-profit hospitals get `fap_url: null` + `nonprofit: false`: they have no
26 CFR 1.501(r) obligation, and the product must say so honestly rather than
apply a charity-care front that doesn't exist for them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

CBI_BASE = "https://www.communitybenefitinsight.org/api/get_hospitals.php"
# Metastore lookup keeps the actual CSV download URL (which embeds a content
# hash and changes when CMS republishes) out of this file -- hardcoding the
# hash would silently go stale.
HGI_DATASET_METADATA_URL = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/xubh-q36u"
)

# CMS mandates this filename shape (45 CFR 180.40): <ein>_<name>_standardcharges.<ext>
_MRF_FILENAME_EIN_RE = re.compile(r"^(\d{9})_")

_NONPROFIT_OWNERSHIP_MARKERS = ("non-profit", "nonprofit")
_GOVERNMENT_OWNERSHIP_MARKERS = ("government",)


@dataclass(frozen=True)
class CbiHospital:
    ein: str
    ccn: str
    name: str
    state: str
    city: str
    address: str
    zip_code: str
    bed_count: str | None


def ein_from_mrf_filename(filename: str) -> str | None:
    """Extract the EIN embedded in a CMS-mandated MRF filename.

    Primary crosswalk path (spike gate (b)). Returns None rather than raising
    when a hospital's filename doesn't follow the convention -- some smaller
    systems don't comply; fall back to `crosswalk_by_cbi`.
    """
    m = _MRF_FILENAME_EIN_RE.match(filename)
    return m.group(1) if m else None


def fetch_cbi_state(state: str, *, timeout: int = 20) -> list[CbiHospital]:
    """Fetch the EIN<->CCN crosswalk for one state from the live CBI API."""
    resp = requests.get(CBI_BASE, params={"state": state.upper()}, timeout=timeout)
    resp.raise_for_status()
    out = []
    for row in resp.json():
        out.append(
            CbiHospital(
                ein=row.get("ein", "").strip(),
                ccn=row.get("medicare_provider_number", "").strip(),
                name=row.get("name", ""),
                state=row.get("state", ""),
                city=row.get("city", ""),
                address=row.get("street_address", ""),
                zip_code=row.get("zip_code", ""),
                bed_count=row.get("hospital_bed_count"),
            )
        )
    return out


def build_ein_ccn_map(states: list[str], *, timeout: int = 20) -> dict[str, CbiHospital]:
    """EIN -> CbiHospital, across the given states. Skips hospitals with no EIN."""
    out: dict[str, CbiHospital] = {}
    for state in states:
        for h in fetch_cbi_state(state, timeout=timeout):
            if h.ein:
                out[h.ein] = h
    return out


def fetch_hospital_general_info_url(*, timeout: int = 20) -> str:
    """Resolve today's Hospital General Information CSV download URL."""
    resp = requests.get(HGI_DATASET_METADATA_URL, timeout=timeout)
    resp.raise_for_status()
    dist = resp.json()["distribution"][0]
    return dist["downloadURL"]


def fetch_hospital_general_info(*, timeout: int = 60) -> dict[str, dict]:
    """CCN (`Facility ID`) -> {name, ownership, hospital_type, ...}.

    Source: CMS Hospital General Information, data.cms.gov dataset
    xubh-q36u. No API key required.
    """
    url = fetch_hospital_general_info_url(timeout=timeout)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    import csv
    import io

    out: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        ccn = row.get("Facility ID", "").strip()
        if not ccn:
            continue
        out[ccn] = row
    return out


def is_nonprofit(ownership: str | None) -> bool | None:
    """Map a CMS 'Hospital Ownership' string to a 501(r)-relevant bool.

    None means "unknown" (e.g. no ownership record found) -- callers should
    treat unknown as "don't assume a charity-care front applies", not as
    for-profit.
    """
    if not ownership:
        return None
    low = ownership.lower()
    if any(m in low for m in _NONPROFIT_OWNERSHIP_MARKERS):
        return True
    if any(m in low for m in _GOVERNMENT_OWNERSHIP_MARKERS):
        # Government hospitals aren't 501(c)(3) nonprofits and carry no
        # 26 CFR 1.501(r) duty either -- same honest "no 501(r) front" path
        # as for-profit, per the persona brief.
        return False
    # Everything else CMS's Hospital Ownership column uses is for-profit:
    # "Proprietary", "Physician", "Tribal", etc.
    return False
