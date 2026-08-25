"""Facility -> hospital record aggregation + seed selection.

docs/SPIKE.md's verdict on LEDGER's own acceptance bar ("≥60% of seeded
nonprofits resolve a live FAP URL"): **select, don't sample.** Random
sampling of Schedule H facilities yields ~26% live URLs (0.318 usable ×
0.83 live) and fails the bar; selecting facilities whose line 16a is already
a real (or repairably real) URL, then live-checking in priority order, is
required to clear it. This module is that selection step.

Priority order for the 200-hospital seed (BUILD_PLAYBOOK §4 persona 2):
demo systems named in the spike (Advocate, Sutter, Kaiser) first, then the
demo states CA/IL, then everything else with a usable/repaired URL --
worst-first is never an option here (scope discipline: "25 well-chosen
hospitals ... beats 200 that mostly fail").
"""

from __future__ import annotations

import concurrent.futures
from collections import Counter
from dataclasses import dataclass, field

import requests

from datapipes.schedule_h import Facility, OrgReturn

DEMO_SYSTEM_NAME_MARKERS = ("ADVOCATE", "SUTTER", "KAISER")
DEMO_STATES = ("IL", "CA")


@dataclass
class HospitalCandidate:
    """A `hospitals/{ein}` candidate, aggregated from one org's facilities."""

    ein: str
    name: str | None
    state: str | None
    tax_period_end: str | None
    free_care_max_fpl_pct: int | None
    discounted_care_max_fpl_pct: int | None
    fap_url: str | None
    fap_app_url: str | None
    url_status: str
    facility_count: int
    facility_names: list[str] = field(default_factory=list)
    quirks: list[str] = field(default_factory=list)
    live: bool | None = None  # filled in by verify_live_batch


def _mode(values: list[int]) -> int | None:
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def aggregate_org(org: OrgReturn) -> HospitalCandidate:
    """Collapse an org's per-facility rows into one `hospitals/{ein}` candidate.

    Contract §3.1 keys `hospitals/{ein}` one record per EIN, but a Schedule H
    filing reports facilities individually and thresholds occasionally vary
    across an org's own facilities (the spike saw this on Kaiser's filing).
    We take the modal (most common) threshold across facilities and flag the
    variance rather than silently picking one -- an agent or reviewer reading
    `quirks` should be able to tell this happened.
    """
    free_vals = [f.free_care_max_fpl_pct for f in org.facilities if f.free_care_max_fpl_pct]
    disc_vals = [
        f.discounted_care_max_fpl_pct for f in org.facilities if f.discounted_care_max_fpl_pct
    ]
    quirks: list[str] = []
    if len(set(free_vals)) > 1:
        quirks.append(f"free-care threshold varies across facilities: {sorted(set(free_vals))}")
    if len(set(disc_vals)) > 1:
        quirks.append(
            f"discounted-care threshold varies across facilities: {sorted(set(disc_vals))}"
        )

    # Best URL among facilities: usable beats repaired beats nothing.
    best: Facility | None = None
    for fac in org.facilities:
        if fac.fap_url is None:
            continue
        if best is None or best.fap_url_status == "repaired" and fac.fap_url_status == "usable":
            best = fac
    for fac in org.facilities:
        quirks.extend(f"[{fac.name or fac.facility_num}] {q}" for q in fac.quirks)

    return HospitalCandidate(
        ein=org.ein,
        name=org.org_name,
        state=org.state,
        tax_period_end=org.tax_period_end,
        free_care_max_fpl_pct=_mode(free_vals),
        discounted_care_max_fpl_pct=_mode(disc_vals),
        fap_url=best.fap_url if best else None,
        fap_app_url=(best.fap_app_url_raw if best else None),
        url_status=best.fap_url_status if best else "blank",
        facility_count=len(org.facilities),
        facility_names=[f.name for f in org.facilities if f.name],
        quirks=quirks,
    )


def _priority(c: HospitalCandidate) -> tuple[int, int]:
    name = (c.name or "").upper()
    is_demo_system = any(marker in name for marker in DEMO_SYSTEM_NAME_MARKERS)
    is_demo_state = (c.state or "").upper() in DEMO_STATES
    url_rank = 0 if c.url_status == "usable" else 1  # usable beats repaired
    if is_demo_system:
        return (0, url_rank)
    if is_demo_state:
        return (1, url_rank)
    return (2, url_rank)


def rank_candidates(candidates: list[HospitalCandidate]) -> list[HospitalCandidate]:
    """Sort so demo systems, then demo states, then everyone else with a URL come first."""
    usable = [c for c in candidates if c.fap_url is not None]
    return sorted(usable, key=_priority)


def verify_live(url: str, *, timeout: float = 8.0) -> bool:
    """Best-effort liveness check: does the FAP URL resolve with a 2xx/3xx?

    A HEAD first (cheap), falling back to GET since some hospital sites
    reject HEAD. Any exception (timeout, DNS failure, TLS error, bot-block)
    counts as not-live -- matches the spike's manual check methodology.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code < 400:
            return True
        resp = requests.get(
            url, headers=headers, timeout=timeout, allow_redirects=True, stream=True
        )
        return resp.status_code < 400
    except requests.RequestException:
        return False


def verify_live_batch(
    candidates: list[HospitalCandidate], *, max_workers: int = 16, timeout: float = 8.0
) -> None:
    """Live-check every candidate's `fap_url` in place (sets `.live`), concurrently."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(verify_live, c.fap_url, timeout=timeout): c for c in candidates if c.fap_url
        }
        for fut in concurrent.futures.as_completed(futures):
            futures[fut].live = fut.result()


def select_seed(
    candidates: list[HospitalCandidate], target: int, *, verify: bool = True
) -> list[HospitalCandidate]:
    """Pick up to `target` hospitals, live-checking in priority order.

    Stops as soon as `target` LIVE hospitals are found (not just
    URL-plausible ones), consuming the ranked pool in priority order. If the
    pool runs out first, returns fewer than `target` -- honest under-seeding
    beats padding with dead URLs (persona brief: "25 well-chosen ... beats
    200 that mostly fail").
    """
    ranked = rank_candidates(candidates)
    if not verify:
        return ranked[:target]

    chosen: list[HospitalCandidate] = []
    # Verify in priority-ordered chunks so we don't pay for live-checking the
    # whole pool when the front of the ranking already clears the target.
    chunk_size = max(target * 2, 50)
    i = 0
    while len(chosen) < target and i < len(ranked):
        chunk = ranked[i : i + chunk_size]
        verify_live_batch(chunk)
        chosen.extend(c for c in chunk if c.live)
        i += chunk_size
    return chosen[:target]
