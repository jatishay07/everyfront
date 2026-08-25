"""CLI: `python -m datapipes.seed --hospitals 200`.

End-to-end WO1 pipeline: IRS bulk Schedule H -> repair + select -> CBI/CMS
crosswalk for CCN + nonprofit status -> Firestore `hospitals/{ein}` + a CSV
mirror in `ef-datasets` (persona 2 acceptance criterion).

This step needs real network access (IRS, CBI, data.cms.gov) and, unless
--dry-run, a live GCP project. It is not part of the pytest suite (which
must run offline in CI) -- exercise it manually or via a nightly job. Unit
tests cover every pure function it calls (`schedule_h`, `select`, `crosswalk`
classification, `firestore_sink.to_contract_record`) against fixed/real
sample data instead.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

from datapipes import crosswalk, irs_bulk, mrf
from datapipes.firestore_sink import get_sink, to_contract_record
from datapipes.gcs_sink import upload_file
from datapipes.schedule_h import parse_return
from datapipes.select import HospitalCandidate, aggregate_org, select_seed

log = logging.getLogger("datapipes.seed")

DEFAULT_YEAR = 2024
DEFAULT_BATCH_ID = "2024_TEOS_XML_11A"  # validated in docs/SPIKE.md gate (a)


def scan_batch_for_schedule_h(year: int, batch_id: str, cache_dir: Path):
    """Download (if needed) the index + one batch zip, yield every OrgReturn
    in that batch that reports at least one Schedule H facility."""
    index_path = cache_dir / f"index_{year}.csv"
    if not index_path.exists():
        log.info("downloading index for %s", year)
        irs_bulk.download_index(year, index_path)

    rows = [r for r in irs_bulk.read_index(index_path) if r.xml_batch_id == batch_id]
    log.info("%d full-990 filings in batch %s", len(rows), batch_id)

    zip_path = cache_dir / f"{batch_id}.zip"
    if not zip_path.exists():
        log.info("downloading batch %s (~1.1 GB)", batch_id)
        irs_bulk.download_batch(year, batch_id, cache_dir)

    n_scanned = 0
    n_hits = 0
    for row, xml_bytes in irs_bulk.iter_member_bytes(zip_path, rows):
        n_scanned += 1
        try:
            org = parse_return(xml_bytes, source=row.object_id)
        except ValueError as exc:
            log.warning("skipping %s: %s", row.object_id, exc)
            continue
        if org.facilities:
            n_hits += 1
            yield org
    log.info("scanned %d filings, %d carried Schedule H facility rows", n_scanned, n_hits)


def discover_mrf_urls(
    chosen: list[HospitalCandidate], *, max_workers: int = 16, timeout: float = 6.0
) -> dict[str, str]:
    """Best-effort WO5 enrichment: guess each hospital's site from its FAP
    URL's host, check for `/cms-hpt.txt`, and keep the first MRF pointer.

    This does NOT download any actual MRF (those run to hundreds of MB --
    see `mrf.py`); it only resolves the *pointer*, which is cheap. A miss
    (no cms-hpt.txt at the guessed host, timeout, wrong domain guess) is
    expected for most of a 200-hospital seed and is silently skipped --
    `mrf.py`'s own acceptance bar (>=3 real demo hospitals, confirmed
    end-to-end against Advocate/Cedars-Sinai/Stanford) does not depend on
    this bulk guess succeeding broadly.
    """
    ein_to_domain = {}
    for c in chosen:
        if not c.fap_url:
            continue
        host = urlsplit(c.fap_url).netloc
        if host:
            ein_to_domain[c.ein] = host

    found: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(mrf.fetch_cms_hpt, domain, timeout=timeout): ein
            for ein, domain in ein_to_domain.items()
        }
        for fut in concurrent.futures.as_completed(futures):
            ein = futures[fut]
            try:
                pointers = fut.result()
            except Exception:
                continue
            for ptr in pointers:
                if ptr.mrf_url:
                    found[ein] = ptr.mrf_url
                    break
    return found


def run(
    *,
    target: int,
    year: int,
    batch_id: str,
    cache_dir: Path,
    out_csv: Path,
    dry_run: bool,
    verify_live: bool,
    with_mrf_discovery: bool = False,
) -> int:
    cache_dir.mkdir(parents=True, exist_ok=True)

    orgs = list(scan_batch_for_schedule_h(year, batch_id, cache_dir))
    candidates = [aggregate_org(o) for o in orgs]
    log.info(
        "%d org-level candidates, %d with a usable/repaired FAP URL",
        len(candidates),
        sum(1 for c in candidates if c.fap_url),
    )

    chosen = select_seed(candidates, target, verify=verify_live)
    log.info("selected %d hospitals (target was %d)", len(chosen), target)
    if verify_live and chosen:
        live_rate = sum(1 for c in chosen if c.live) / len(chosen)
        log.info("live FAP URL rate among selected: %.1f%%", live_rate * 100)

    states = sorted({c.state for c in chosen if c.state})
    log.info("building EIN<->CCN crosswalk for states: %s", states)
    try:
        cbi_map = crosswalk.build_ein_ccn_map(states) if states else {}
    except Exception as exc:  # network dependency -- degrade, don't crash the seed
        log.warning("CBI crosswalk unavailable (%s); CCN/nonprofit will be unknown", exc)
        cbi_map = {}
    try:
        hgi_map = crosswalk.fetch_hospital_general_info() if cbi_map else {}
    except Exception as exc:
        log.warning("CMS Hospital General Information unavailable (%s)", exc)
        hgi_map = {}

    mrf_urls: dict[str, str] = {}
    if with_mrf_discovery and chosen:
        log.info("discovering cms-hpt.txt MRF pointers for %d hospitals (best-effort)", len(chosen))
        mrf_urls = discover_mrf_urls(chosen)
        log.info("resolved an MRF pointer for %d/%d hospitals", len(mrf_urls), len(chosen))

    sink = get_sink(dry_run=dry_run, local_path=cache_dir / "hospitals_seed.jsonl")
    rows_for_csv = []
    for c in chosen:
        cbi = cbi_map.get(c.ein)
        ccn = cbi.ccn if cbi else None
        ownership = hgi_map.get(ccn, {}).get("Hospital Ownership") if ccn else None
        # Every candidate here came FROM a Schedule H Part V Section B filing
        # (schedule_h.py only emits facility rows out of IRS990ScheduleH),
        # and only a 26 CFR 1.501(r)-4 "hospital organization" -- i.e. a
        # 501(c)(3) nonprofit -- files that schedule at all. So the filing
        # itself is stronger, more direct evidence of nonprofit status than
        # CMS's Hospital Ownership column, which occasionally tags a
        # 501(c)(3)-operated public hospital district as "Government"
        # (observed on a real seeded record: Marin General Hospital). Trust
        # the Schedule H origin; use HGI ownership only to flag a genuine
        # conflict for manual review, never to overwrite it to False.
        nonprofit = True
        if ownership and crosswalk.is_nonprofit(ownership) is False:
            log.warning(
                "%s (%s): filed Schedule H (nonprofit) but CMS Hospital "
                "Ownership says %r -- keeping nonprofit=True, flagging for review",
                c.name,
                c.ein,
                ownership,
            )
            c.quirks.append(
                f"CMS Hospital Ownership reports {ownership!r} despite Schedule H filing"
            )
        record = to_contract_record(c, ccn=ccn, nonprofit=nonprofit, mrf_url=mrf_urls.get(c.ein))
        sink.write(c.ein, record)
        rows_for_csv.append({"ein": c.ein, **record, "quirks": "; ".join(record["quirks"])})

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if rows_for_csv:
        with open(out_csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows_for_csv[0].keys()))
            writer.writeheader()
            writer.writerows(rows_for_csv)
        log.info("wrote %s (%d rows)", out_csv, len(rows_for_csv))
        uri = upload_file(out_csv, f"hospitals/{out_csv.name}", dry_run=dry_run)
        if uri:
            log.info("uploaded to %s", uri)

    return len(chosen)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hospitals", type=int, default=200, help="target seed size")
    p.add_argument("--year", type=int, default=DEFAULT_YEAR)
    p.add_argument("--batch-id", default=DEFAULT_BATCH_ID)
    p.add_argument("--cache-dir", type=Path, default=Path(".datapipes_cache"))
    p.add_argument("--out-csv", type=Path, default=Path(".datapipes_cache/hospitals_seed.csv"))
    p.add_argument("--dry-run", action="store_true", help="skip Firestore/GCS writes")
    p.add_argument(
        "--no-live-check",
        action="store_true",
        help="skip HTTP liveness verification (faster, but risks the ≥60% bar)",
    )
    p.add_argument(
        "--with-mrf-discovery",
        action="store_true",
        help="best-effort cms-hpt.txt MRF pointer lookup per hospital (WO5 enrichment)",
    )
    args = p.parse_args(argv)

    n = run(
        target=args.hospitals,
        year=args.year,
        batch_id=args.batch_id,
        cache_dir=args.cache_dir,
        out_csv=args.out_csv,
        dry_run=args.dry_run,
        verify_live=not args.no_live_check,
        with_mrf_discovery=args.with_mrf_discovery,
    )
    print(f"seeded {n} hospitals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
