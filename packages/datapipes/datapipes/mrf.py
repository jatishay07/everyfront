"""`cms-hpt.txt` -> Machine-Readable File (MRF) -> cash price.

Work order 5. 45 CFR Part 180 (Hospital Price Transparency) requires every
hospital to publish `<domain>/cms-hpt.txt`, a small key:value pointer file
naming the location(s) of its real machine-readable standard-charge file.
docs/SPIKE.md gate (b) reached a live MRF for 7 of 8 real hospital systems
this way and PASSED.

Traps this module handles (all confirmed against real files, 2026-08-25):

1. **User-Agent matters.** `cedars-sinai.org/cms-hpt.txt` 403s with no UA and
   200s with a normal browser UA. Some hospital CDNs (Cloudflare/Akamai) gate
   on this. Always send one.
2. **`cms-hpt.txt` needs redirects followed.** `advocatehealth.com` returns a
   bare relative path on the first response; the real content is behind a
   redirect.
3. **Two MRF formats in the wild.** CSV (Advocate: 3-row header -- row 1 is
   the attestation text, row 2 is hospital metadata, row 3 is the real column
   header, row 4+ is data) and CMS's JSON schema v2.x (Cedars-Sinai,
   Stanford): `standard_charge_information[].code_information[].code` /
   `.standard_charges[].{gross_charge,discounted_cash}`.
4. **MRFs are huge and most rows are empty cash prices** (payer-specific
   negotiated rows with no expressible dollar amount). Stanford's is 154 MB;
   Cedars-Sinai's is ~880 MB. Never load one whole:
   - CSV: stream line-by-line with `csv.reader`, skip the first 2 rows.
   - JSON: stream with `ijson` (`standard_charge_information.item`), which
     never materializes the whole document.
   Both paths filter to `discounted_cash` (`standard_charge|discounted_cash`
   in CSV) being present before yielding a result.
5. **Some servers ignore HTTP Range and just send the whole file anyway**
   (confirmed on an Azure-blob-hosted CSV that answered a `bytes=0-200000`
   range request with a full 200 instead of a 206). This module never
   depends on Range being honored for correctness -- it always stops reading
   once it has what it needs (a match per requested code) rather than
   trusting a partial-content status.
6. **~1/3 of published MRFs are unusable** (GAO). `fetch_cms_hpt` and
   `fetch_cash_prices` return `None`/`[]` on any parse failure, timeout, or
   unexpected schema -- they never raise out of a batch run.
7. **A JSON MRF can carry a leading UTF-8 BOM.** Confirmed live 2026-08-25 on
   Stanford Health Care's real MRF (gate (b), 154 MB): a byte-order mark
   before the opening `{` makes ijson's C backend raise on the first token.
   That exception IS caught by trap (6)'s safety net, so this used to fail
   completely silently -- `_BomStrippingStream` strips it before ijson ever
   sees the stream.
8. **The same code can appear on more than one CSV row at different prices.**
   Confirmed live on Advocate's real MRF: CPT 86787 bills both "AB,
   VARICELLA ZOSTER IGG" ($70.00 cash) and "...IGM" ($72.50 cash) as
   separate rows under the identical code. `_parse_csv_mrf` now stops
   matching a code once its first row is found, so the answer is the first
   occurrence in file order -- deterministic, and consistent with
   docs/SPIKE.md gate (b)'s recorded $140.00/$70.00 figure for this exact
   code. (The JSON path already had this guard.)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

import ijson
import requests

_HEADERS = {
    # Real UA required -- see module docstring trap (1).
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class MrfPointer:
    location_name: str | None
    source_page_url: str | None
    mrf_url: str | None
    contact_name: str | None
    contact_email: str | None


@dataclass(frozen=True)
class CashPrice:
    code: str
    code_type: str | None
    description: str | None
    gross: float | None
    cash: float


def fetch_cms_hpt(domain: str, *, timeout: int = 15) -> list[MrfPointer]:
    """GET `https://{domain}/cms-hpt.txt` and parse it into pointer blocks.

    The file is a sequence of `key: value` lines; a hospital system with
    multiple locations repeats `location-name:` to start a new block. Returns
    [] (never raises) on any network/format failure -- see trap (6).
    """
    url = domain if domain.startswith("http") else f"https://{domain}/cms-hpt.txt"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        text = resp.text
    except requests.RequestException:
        return []

    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "location-name" and current:
            blocks.append(current)
            current = {}
        current[key] = value
    if current:
        blocks.append(current)
    if not blocks:
        return []

    return [
        MrfPointer(
            location_name=b.get("location-name"),
            source_page_url=b.get("source-page-url"),
            mrf_url=b.get("mrf-url"),
            contact_name=b.get("contact-name"),
            contact_email=b.get("contact-email"),
        )
        for b in blocks
    ]


_MAX_ROWS_SCANNED = 2_000_000  # safety net so a pathological file can't hang a batch run


def _parse_csv_mrf(resp: requests.Response, codes: set[str]) -> list[CashPrice]:
    """Stream a CMS-standard-charges CSV MRF. See trap (3)/(4).

    Stops early once every requested code has at least one cash-priced
    match -- these files run to hundreds of MB and most codes only need to
    be confirmed once, not exhaustively enumerated.
    """
    out: list[CashPrice] = []
    found: set[str] = set()
    # iter_lines gives us a line-by-line stream without materializing the
    # whole (often 100+ MB) body. Decode manually rather than via
    # iter_lines(decode_unicode=True): that path silently returns bytes when
    # requests can't confirm an encoding, which crashes csv.reader downstream.
    lines = (raw.decode("utf-8", errors="replace") for raw in resp.iter_lines())
    try:
        next(lines)  # row 1: attestation text
        next(lines)  # row 2: hospital metadata
        header_line = next(lines)  # row 3: real column header
    except StopIteration:
        return []
    header = next(csv.reader([header_line]))
    idx = {name: i for i, name in enumerate(header)}
    code_col = idx.get("code|1")
    gross_col = idx.get("standard_charge|gross")
    cash_col = idx.get("standard_charge|discounted_cash")
    desc_col = idx.get("description")
    type_col = idx.get("code|1|type")
    if code_col is None or cash_col is None:
        return []

    for scanned, line in enumerate(lines):
        if not line:
            continue
        if found >= codes or scanned >= _MAX_ROWS_SCANNED:
            break
        row = next(csv.reader([line]))
        if len(row) <= code_col:
            continue
        code = row[code_col].strip()
        if code not in codes or code in found:
            # trap (8): a hospital can bill the SAME code under different
            # descriptions at different price points (confirmed live,
            # Advocate's real MRF: 86787 appears both as "AB, VARICELLA
            # ZOSTER IGG" at $70.00 cash and "...IGM" at $72.50). Without
            # this check every matching row got appended and the LAST one
            # in file order silently won -- contradicting this function's
            # own "first match wins" docstring and, concretely, diverging
            # from the exact $140/$70 figure docs/SPIKE.md gate (b)
            # recorded for this code from the same file.
            continue
        cash_raw = row[cash_col].strip() if len(row) > cash_col else ""
        if not cash_raw:
            continue  # payer-specific negotiated row with no cash price -- trap (4)
        try:
            cash = float(cash_raw)
        except ValueError:
            continue
        gross_raw = row[gross_col].strip() if gross_col is not None and len(row) > gross_col else ""
        gross = float(gross_raw) if gross_raw else None
        out.append(
            CashPrice(
                code=code,
                code_type=row[type_col] if type_col is not None and len(row) > type_col else None,
                description=row[desc_col] if desc_col is not None and len(row) > desc_col else None,
                gross=gross,
                cash=cash,
            )
        )
        found.add(code)
    return out


class _BomStrippingStream:
    """Wraps a file-like object and drops a leading UTF-8 BOM (`EF BB BF`).

    Trap (7), found live 2026-08-25 running LEDGER's WO6 audit fix: Stanford
    Health Care's real MRF (docs/SPIKE.md gate (b), 154 MB JSON, confirmed
    live) is emitted with a UTF-8 byte-order mark before the opening `{`.
    ijson's C backend (`yajl2_c`) treats that as a lexical error and raises
    `ijson.common.IncompleteJSONError` on the very first token -- which
    `fetch_cash_prices`'s own `except ijson.JSONError` catches (it IS a
    JSONError subclass), so the failure was never loud: it just silently
    returned `[]` for a hospital whose MRF gate (b) had already proven
    reachable and parseable. A BOM is legal UTF-8 and not unusual coming out
    of Windows-authored tooling, so this is likely to recur for other
    hospitals, not a one-off.

    `requests`' streaming reader can call `.read(0)` as a priming probe
    before ever reading real bytes (confirmed empirically), so the BOM check
    must trigger on the first *non-empty* chunk, not the first call.
    """

    def __init__(self, raw) -> None:
        self._raw = raw
        self._checked = False

    def read(self, n: int = -1) -> bytes:
        chunk = self._raw.read(n)
        if not self._checked and chunk:
            self._checked = True
            if chunk[:3] == b"\xef\xbb\xbf":
                chunk = chunk[3:]
        return chunk


def _parse_json_mrf(resp: requests.Response, codes: set[str]) -> list[CashPrice]:
    """Stream a CMS-standard JSON-schema MRF with ijson. See trap (3)/(4)/(7).

    Same early-exit as the CSV path: these files run to hundreds of MB (one
    real Cedars-Sinai MRF checked during development was ~880 MB), and
    ijson.items already streams rather than materializing the DOM, but we
    still stop as soon as every requested code is confirmed.
    """
    out: list[CashPrice] = []
    found: set[str] = set()
    resp.raw.decode_content = True
    stream = _BomStrippingStream(resp.raw)
    for scanned, item in enumerate(ijson.items(stream, "standard_charge_information.item")):
        if found >= codes or scanned >= _MAX_ROWS_SCANNED:
            break
        code_infos = item.get("code_information") or []
        matched = [c for c in code_infos if c.get("code") in codes]
        if not matched:
            continue
        for sc in item.get("standard_charges") or []:
            cash = sc.get("discounted_cash")
            if cash is None:
                continue
            gross = sc.get("gross_charge")
            for c in matched:
                if c.get("code") in found:
                    continue
                out.append(
                    CashPrice(
                        code=c.get("code"),
                        code_type=c.get("type"),
                        description=item.get("description"),
                        gross=float(gross) if gross is not None else None,
                        cash=float(cash),
                    )
                )
                found.add(c.get("code"))
            if found >= codes:
                break
    return out


def fetch_cash_prices(
    mrf_url: str, codes: list[str], *, timeout: int = 60, max_results: int | None = None
) -> list[CashPrice]:
    """Fetch attested cash prices for `codes` from a hospital's MRF.

    Returns [] on any failure (unreachable, unrecognized format, no matches)
    -- this is the boundary the rest of the pipeline depends on never
    crashing at (trap (6): ~1/3 of real MRFs are unusable per GAO).
    """
    code_set = set(codes)
    is_json = mrf_url.lower().endswith(".json")
    try:
        with requests.get(mrf_url, headers=_HEADERS, timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if is_json or "json" in content_type:
                results = _parse_json_mrf(resp, code_set)
            else:
                results = _parse_csv_mrf(resp, code_set)
    except (requests.RequestException, OSError, ijson.JSONError):
        return []
    if max_results is not None:
        results = results[:max_results]
    return results


def discover_and_fetch(
    domain: str, codes: list[str], *, timeout: int = 60
) -> tuple[str | None, list[CashPrice]]:
    """Convenience: cms-hpt.txt -> first MRF url -> cash prices for `codes`.

    Returns (mrf_url_used_or_None, prices). Tries every pointer block in
    order until one yields results, since a multi-location system may list
    several MRFs and not all are reachable/parseable.
    """
    for ptr in fetch_cms_hpt(domain, timeout=timeout):
        if not ptr.mrf_url:
            continue
        prices = fetch_cash_prices(ptr.mrf_url, codes, timeout=timeout)
        if prices:
            return ptr.mrf_url, prices
    return None, []
