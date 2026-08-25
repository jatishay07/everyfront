"""NCCI PTP + MUE tables: load once, query in <10ms. Work order 3.

**Licensing boundary (read before changing the download URLs).** CPT codes
and descriptors are AMA copyrighted. This module downloads only from CMS
pages that do NOT require an AMA license click-through:

- MUE tables ("Outpatient Hospital Services MUE table") are published
  directly by CMS with no gate -- confirmed 2026-08-25, downloaded straight
  from `cms.gov/files/zip/...mue-table.zip`.
- The FULL baseline PTP edit tables ("...ptp-edits-ccioph-...") ARE
  AMA-license-gated (`cms.gov/license/ama?file=...`); fetching them
  programmatically returns the license click-through HTML, not the zip, and
  this module does not attempt to bypass that. Instead it uses the
  **quarterly additions/deletions files**, which CMS publishes on the open,
  non-gated download links on the same page -- these are code-pairs +
  modifier indicator only (no descriptors), which is exactly the
  "codes and edit flags only" scope this persona is bound to. The tradeoff,
  stated honestly: this is the current quarter's *change list*, not the full
  historical baseline (that would require a human to click through the AMA
  license once, then hand the resulting file to this loader).
- Neither the MUE CSV nor the PTP txt files carry CPT code descriptions --
  the MUE "rationale" column is CMS's own category label (e.g. "Date of
  Service Edit: Policy"), not AMA descriptor text, so it's safe to keep.

Storage: a local sqlite file (per persona brief: "sqlite or parquet in GCS"),
indexed on the lookup keys, so `lookup`/`mue` answer in << 10ms even for the
full PTP+MUE corpus. See `build_db` / `NCCITable`.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

# Open, non-gated CMS download links (verified live 2026-08-25). Update the
# quarter string when CMS posts a new one -- these are stable URL patterns,
# not hashed/rotating like the data.cms.gov distribution URLs.
MUE_HOSPITAL_TABLE_ZIP = (
    "https://www.cms.gov/files/zip/"
    "medicare-ncci-2026-q3-facility-outpatient-hospital-services-mue-table.zip"
)
PTP_HOSPITAL_QUARTERLY_CHANGES_ZIP = (
    "https://www.cms.gov/files/zip/"
    "medicare-ncci-2026q3-hospital-quarterly-additions-deletions-revisions-ptp.zip"
)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


@dataclass(frozen=True)
class PtpResult:
    matched: bool
    column1: str | None = None
    column2: str | None = None
    modifier_indicator: int | None = None
    allowed_with_modifier: bool | None = None
    source: str | None = None  # "addition" | "deletion"

    def explain(self) -> str:
        if not self.matched:
            return "No NCCI PTP edit found for this code pair."
        if self.modifier_indicator == 0:
            return (
                f"NCCI PTP edit: {self.column1} is column 1, {self.column2} is column 2. "
                "Modifier indicator 0 -- this pair may NEVER be billed together, "
                "with or without a modifier."
            )
        if self.modifier_indicator == 1:
            return (
                f"NCCI PTP edit: {self.column1} is column 1, {self.column2} is column 2. "
                "Modifier indicator 1 -- billable together only with an appropriate "
                "NCCI-associated modifier documenting a separate, distinct service."
            )
        return (
            f"NCCI PTP edit: {self.column1} is column 1, {self.column2} is column 2. "
            "Modifier indicator 9 -- edit does not apply (deleted/not applicable)."
        )


@dataclass(frozen=True)
class MueResult:
    code: str
    mue_value: int
    adjudication_indicator: str | None
    rationale: str | None

    def explain(self) -> str:
        return (
            f"{self.code}: Medically Unlikely Edit ceiling is {self.mue_value} unit(s) "
            f"per line/date of service ({self.rationale or 'no rationale published'})."
        )


def _iter_ptp_rows(path: Path, source: str):
    with open(path, encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter="\t")
        rows = list(reader)
    # Row 0: AMA copyright notice (single cell). Row 1: header
    # ("Column 1", "Column 2", "Modifier\nIndicator\n..."). Data from row 2.
    for row in rows[2:]:
        if len(row) < 3 or not row[0].strip():
            continue
        col1, col2, mod = row[0].strip(), row[1].strip(), row[2].strip()
        try:
            mod_int = int(mod)
        except ValueError:
            continue
        yield col1, col2, mod_int, source


def _iter_mue_rows(path: Path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    # Row 0: AMA copyright notice. Row 1: header. Data from row 2.
    for row in rows[2:]:
        if len(row) < 3 or not row[0].strip():
            continue
        code = row[0].strip()
        try:
            mue_value = int(float(row[1].strip()))
        except (ValueError, IndexError):
            continue
        adjudication = row[2].strip() if len(row) > 2 else None
        rationale = row[3].strip() if len(row) > 3 else None
        yield code, mue_value, adjudication, rationale


def build_db(
    *,
    mue_csv: Path,
    ptp_additions_txt: Path,
    ptp_deletions_txt: Path | None,
    out_path: Path,
) -> Path:
    """Build the local sqlite lookup table from downloaded CMS files."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(out_path)
    try:
        conn.execute(
            "CREATE TABLE ptp_edits ("
            "column1 TEXT NOT NULL, column2 TEXT NOT NULL, "
            "modifier_indicator INTEGER NOT NULL, source TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE mue ("
            "code TEXT PRIMARY KEY, mue_value INTEGER NOT NULL, "
            "adjudication_indicator TEXT, rationale TEXT)"
        )

        ptp_rows = list(_iter_ptp_rows(ptp_additions_txt, "addition"))
        if ptp_deletions_txt is not None:
            ptp_rows += list(_iter_ptp_rows(ptp_deletions_txt, "deletion"))
        conn.executemany(
            "INSERT INTO ptp_edits (column1, column2, modifier_indicator, source) "
            "VALUES (?, ?, ?, ?)",
            ptp_rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO mue (code, mue_value, adjudication_indicator, rationale) "
            "VALUES (?, ?, ?, ?)",
            list(_iter_mue_rows(mue_csv)),
        )

        conn.execute("CREATE INDEX idx_ptp_pair ON ptp_edits (column1, column2)")
        conn.execute("CREATE INDEX idx_ptp_pair_rev ON ptp_edits (column2, column1)")
        conn.commit()
    finally:
        conn.close()
    return out_path


def _download_zip_member(url: str, name_suffix: str, *, timeout: int = 60) -> bytes:
    """Download a CMS zip and return the one member ending in `name_suffix`.

    CMS's zips carry a matching .xlsx alongside the .csv/.txt we want; this
    picks the plain-text one so `build_db`'s csv readers never have to deal
    with the spreadsheet format.
    """
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        matches = [n for n in zf.namelist() if n.endswith(name_suffix)]
        if not matches:
            raise ValueError(f"no member ending in {name_suffix!r} in {url}")
        return zf.read(matches[0])


def download_and_build(out_dir: Path) -> Path:
    """Download the current-quarter CMS files (see module docstring for the
    licensing boundary that shapes exactly which files these are) and build
    the sqlite lookup table in one step. Network-dependent; not run in the
    default pytest suite -- see `test_ncci.py`, which exercises `build_db`
    directly against small fixture files shaped like these real ones.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    mue_bytes = _download_zip_member(MUE_HOSPITAL_TABLE_ZIP, ".csv")
    ptp_bytes = _download_zip_member(
        PTP_HOSPITAL_QUARTERLY_CHANGES_ZIP, "Additions_Eff_07-01-2026.txt"
    )
    deletions_bytes = _download_zip_member(
        PTP_HOSPITAL_QUARTERLY_CHANGES_ZIP, "Deletions_Eff_07-01-2026.txt"
    )

    mue_csv = out_dir / "mue_outpatient_hospital.csv"
    mue_csv.write_bytes(mue_bytes)
    additions_txt = out_dir / "ptp_additions.txt"
    additions_txt.write_bytes(ptp_bytes)
    deletions_txt = out_dir / "ptp_deletions.txt"
    deletions_txt.write_bytes(deletions_bytes)

    return build_db(
        mue_csv=mue_csv,
        ptp_additions_txt=additions_txt,
        ptp_deletions_txt=deletions_txt,
        out_path=out_dir / "ncci.sqlite",
    )


class NCCITable:
    """Read-only handle on the sqlite lookup table. <10ms per lookup/mue call."""

    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> NCCITable:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def lookup(self, code_a: str, code_b: str) -> PtpResult:
        """Check whether (code_a, code_b) is an NCCI PTP edit pair, either order.

        NCCI PTP pairs are directional (column 1 = the comprehensive code,
        column 2 = the component normally bundled into it), but a claim can
        present the two billed codes in either order, so this checks both.
        """
        cur = self._conn.execute(
            "SELECT column1, column2, modifier_indicator, source FROM ptp_edits "
            "WHERE (column1 = ? AND column2 = ?) OR (column1 = ? AND column2 = ?) "
            "LIMIT 1",
            (code_a, code_b, code_b, code_a),
        )
        row = cur.fetchone()
        if row is None:
            return PtpResult(matched=False)
        col1, col2, mod, source = row
        return PtpResult(
            matched=True,
            column1=col1,
            column2=col2,
            modifier_indicator=mod,
            allowed_with_modifier=(mod == 1),
            source=source,
        )

    def mue(self, code: str) -> MueResult | None:
        cur = self._conn.execute(
            "SELECT code, mue_value, adjudication_indicator, rationale FROM mue WHERE code = ?",
            (code,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return MueResult(
            code=row[0], mue_value=row[1], adjudication_indicator=row[2], rationale=row[3]
        )


def main(argv: list[str] | None = None) -> int:
    """`python -m datapipes.ncci [--out-dir DIR] [--upload]`

    Downloads the current CMS files, builds the sqlite lookup table, and
    (with --upload) mirrors it to `ef-datasets` for other services to read.
    """
    import argparse
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("datapipes.ncci")

    p = argparse.ArgumentParser(description=main.__doc__)
    p.add_argument("--out-dir", type=Path, default=Path(".datapipes_cache/ncci"))
    p.add_argument("--upload", action="store_true", help="mirror the sqlite db to ef-datasets")
    args = p.parse_args(argv)

    db_path = download_and_build(args.out_dir)
    log.info("built %s", db_path)

    with NCCITable(db_path) as tbl:
        sample = tbl.lookup("0002M", "0468U")
        log.info("sanity check lookup: %s", sample.explain())

    if args.upload:
        from datapipes.gcs_sink import upload_file

        uri = upload_file(db_path, f"ncci/{db_path.name}")
        if uri:
            log.info("uploaded to %s", uri)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
