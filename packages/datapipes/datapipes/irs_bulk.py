"""IRS 990 bulk XML index + batch download.

docs/SPIKE.md gate (a) findings this module encodes:

- The old per-filing endpoint `s3.amazonaws.com/irs-form-990/{object_id}_public.xml`
  is DEAD (404 as of 2026-08). The bulk batch zips under
  `apps.irs.gov/pub/epostcard/990/xml/{year}/` are the only access path.
- Batches are ~1.1 GB and ZIP64. macOS Info-ZIP `unzip` errors on them;
  Python `zipfile` reads them fine (confirmed: 186,632 members in one batch,
  namelist() in well under a second).
- Batch-ID casing is inconsistent in the index (`2024_TEOS_XML_05a` vs
  `..._07A`) and download URLs are CASE-SENSITIVE. This module never
  normalizes the case of a batch id it read from the index.
- The index CSV (`index_{year}.csv`) maps OBJECT_ID -> XML_BATCH_ID -> the
  member filename `{batch_id}/{object_id}_public.xml` inside that batch's
  zip. Filtering the index to RETURN_TYPE == "990" before opening any XML
  cuts a 186k-member batch down to ~101k candidates and lets us skip 990-EZ/
  990-PF/990-T filings, which cannot carry Schedule H Part V Section B.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import requests

BASE_URL = "https://apps.irs.gov/pub/epostcard/990/xml"
# Full Form 990 is the only return type that carries Schedule H.
HOSPITAL_ELIGIBLE_RETURN_TYPES = ("990",)


@dataclass(frozen=True)
class IndexRow:
    ein: str
    taxpayer_name: str
    return_type: str
    object_id: str
    xml_batch_id: str


def index_url(year: int) -> str:
    return f"{BASE_URL}/{year}/index_{year}.csv"


def download_index(year: int, dest: Path, *, timeout: int = 60) -> Path:
    """Download the year's index CSV (small -- tens of MB, not a batch zip)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(index_url(year), timeout=timeout, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            fh.write(chunk)
    return dest


def read_index(
    path: Path, *, return_types: tuple[str, ...] = HOSPITAL_ELIGIBLE_RETURN_TYPES
) -> Iterator[IndexRow]:
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if return_types and row["RETURN_TYPE"] not in return_types:
                continue
            yield IndexRow(
                ein=row["EIN"],
                taxpayer_name=row["TAXPAYER_NAME"],
                return_type=row["RETURN_TYPE"],
                object_id=row["OBJECT_ID"],
                xml_batch_id=row["XML_BATCH_ID"],
            )


def batch_zip_url(year: int, batch_id: str) -> str:
    # batch_id is used verbatim -- download URLs are case-sensitive (spike quirk).
    return f"{BASE_URL}/{year}/{batch_id}.zip"


def download_batch(year: int, batch_id: str, dest_dir: Path, *, timeout: int = 300) -> Path:
    """Stream one ~1.1 GB batch zip to disk. Idempotent: skips if already present."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{batch_id}.zip"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = batch_zip_url(year, batch_id)
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    tmp = dest.with_suffix(".zip.part")
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 22):
            fh.write(chunk)
    tmp.rename(dest)
    return dest


def member_name(batch_id: str, object_id: str) -> str:
    return f"{batch_id}/{object_id}_public.xml"


def iter_member_bytes(zip_path: Path, rows: list[IndexRow]) -> Iterator[tuple[IndexRow, bytes]]:
    """Read only the requested members out of a batch zip.

    Skips rows whose member isn't present (a handful of index rows point to
    objects missing from the batch -- log and move on rather than crash,
    matching the pipeline's "flag, don't guess" rule).
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for row in rows:
            name = member_name(row.xml_batch_id, row.object_id)
            if name not in names:
                continue
            with zf.open(name) as member:
                yield row, member.read()


def open_remote_zip_central_directory(url: str, *, timeout: int = 30) -> zipfile.ZipFile:
    """Read a remote zip's central directory via HTTP range requests only.

    An optimization path for when only a handful of members are needed out
    of a 1.1 GB batch: fetches just the end-of-central-directory + central
    directory records (a few hundred KB even for a 186k-member archive), not
    the whole archive. `zipfile.ZipFile` only needs a file-like object
    supporting `seek`/`read`, which we back with ranged HTTP GETs.
    """
    return zipfile.ZipFile(_HTTPRangeFile(url, timeout=timeout))


class _HTTPRangeFile(io.RawIOBase):
    """Minimal seekable file-like object backed by HTTP Range requests."""

    def __init__(self, url: str, *, timeout: int = 30):
        self._url = url
        self._timeout = timeout
        self._pos = 0
        head = requests.head(url, timeout=timeout, allow_redirects=True)
        head.raise_for_status()
        self._size = int(head.headers["Content-Length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self._size + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def readinto(self, b) -> int:
        n = len(b)
        if n == 0:
            return 0
        start, end = self._pos, self._pos + n - 1
        resp = requests.get(
            self._url,
            headers={"Range": f"bytes={start}-{end}"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.content
        b[: len(data)] = data
        self._pos += len(data)
        return len(data)
