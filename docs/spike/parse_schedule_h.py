"""FORGE day-1 spike, gate (a): IRS Form 990 Schedule H Part V Section B parser.

Proves we can recover a hospital's Financial Assistance Policy facts straight
from the IRS e-file XML -- the raw material for `hospitals/{ein}` (contract 3.1)
and for LEDGER's Schedule H pipeline (persona 2, work order 1).

Fields extracted, per hospital FACILITY (a single 990 covers many facilities):
  line 13a  FPGFamilyIncmLmtFreeCarePct      -> free_care_max_fpl_pct
  line 13a  FPGFamilyIncmLmtDscntCarePct     -> discounted_care_max_fpl_pct
  line 16a  FAPAvailableOnWebsiteURLTxt      -> fap_url
  line 16b  FAPAppAvailableOnWebsiteURLTxt   -> fap_app_url
  line 16c  FAPSummaryOnWebsiteURLTxt        -> fap_summary_url

Source: IRS Form 990 Schedule H, Part V Section B ("Facility Policies and
Practices"), required of hospital organizations under 26 CFR 1.501(r)-4.
Schema namespace http://www.irs.gov/efile, return version 2023v5.1.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

NS = {"efile": "http://www.irs.gov/efile"}

# Part V Section B line number -> XML element name.
LINE_13A_FREE = "FPGFamilyIncmLmtFreeCarePct"
LINE_13A_DISCOUNTED = "FPGFamilyIncmLmtDscntCarePct"
LINE_16A_FAP_URL = "FAPAvailableOnWebsiteURLTxt"
LINE_16B_APP_URL = "FAPAppAvailableOnWebsiteURLTxt"
LINE_16C_SUMMARY_URL = "FAPSummaryOnWebsiteURLTxt"


@dataclass
class Facility:
    ein: str
    tax_period_end: str
    facility_num: str | None
    name: str | None
    free_care_max_fpl_pct: int | None
    discounted_care_max_fpl_pct: int | None
    fap_url: str | None
    fap_app_url: str | None
    fap_summary_url: str | None
    quirks: list[str]


def _text(node, tag: str) -> str | None:
    el = node.find(f"efile:{tag}", NS)
    if el is None or el.text is None:
        return None
    val = el.text.strip()
    return val or None


def _pct(node, tag: str, quirks: list[str]) -> int | None:
    """Thresholds are meant to be integer FPL percentages; log anything else.

    LEDGER work order 1 requires we normalize to int and *flag* unparseable
    rows rather than guess (playbook persona 2).
    """
    raw = _text(node, tag)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        quirks.append(f"{tag}={raw!r} not numeric")
        return None


def parse(path: str) -> list[Facility]:
    root = ET.parse(path).getroot()
    hdr = root.find("efile:ReturnHeader", NS)
    # The filer EIN lives at ReturnHeader/Filer/EIN, not as a direct child of
    # ReturnHeader. It is the Firestore key for hospitals/{ein} (contract 3.1),
    # so a silent "" here would corrupt every downstream lookup.
    ein = hdr.findtext("efile:Filer/efile:EIN", default="", namespaces=NS).strip()
    period = hdr.findtext("efile:TaxPeriodEndDt", default="", namespaces=NS).strip()
    if not ein:
        raise ValueError(f"{path}: could not resolve filer EIN")

    out: list[Facility] = []
    for grp in root.iter(f"{{{NS['efile']}}}HospitalFcltyPoliciesPrctcGrp"):
        quirks: list[str] = []

        name_el = grp.find("efile:HospitalFacilityName", NS)
        name = _text(name_el, "BusinessNameLine1Txt") if name_el is not None else None

        fac = Facility(
            ein=ein,
            tax_period_end=period,
            facility_num=_text(grp, "FacilityNum"),
            name=name,
            free_care_max_fpl_pct=_pct(grp, LINE_13A_FREE, quirks),
            discounted_care_max_fpl_pct=_pct(grp, LINE_13A_DISCOUNTED, quirks),
            fap_url=_text(grp, LINE_16A_FAP_URL),
            fap_app_url=_text(grp, LINE_16B_APP_URL),
            fap_summary_url=_text(grp, LINE_16C_SUMMARY_URL),
            quirks=quirks,
        )
        if fac.fap_url is None:
            fac.quirks.append("line 16a blank -- no FAP URL published in filing")
        out.append(fac)
    return out


if __name__ == "__main__":
    everything = []
    for path in sys.argv[1:]:
        facilities = parse(path)
        print(f"\n=== {path}  ({len(facilities)} facilities) ===")
        for f in facilities:
            print(f"  [{f.facility_num}] {f.name}")
            print(
                f"      13a free/discounted FPL%: "
                f"{f.free_care_max_fpl_pct} / {f.discounted_care_max_fpl_pct}"
            )
            print(f"      16a FAP: {f.fap_url}")
            print(f"      16b app: {f.fap_app_url}")
            if f.quirks:
                print(f"      QUIRKS: {f.quirks}")
        everything += [asdict(f) for f in facilities]
    with open("schedule_h_extract.json", "w") as fh:
        json.dump(everything, fh, indent=2)
    print(f"\nwrote schedule_h_extract.json ({len(everything)} facility rows)")
