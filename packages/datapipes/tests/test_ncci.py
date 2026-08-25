"""Unit tests for datapipes.ncci: build a tiny local db (shaped like the real
CMS files -- row 0 copyright notice, row 1 header, data from row 2) and
verify lookup/mue correctness + the <10ms performance bar (persona 2
acceptance criterion)."""

from __future__ import annotations

import time

import pytest
from datapipes import ncci

COPYRIGHT = "Current Procedural Terminology (CPT) codes... copyright AMA."


@pytest.fixture
def db_path(tmp_path):
    mue_csv = tmp_path / "mue.csv"
    mue_csv.write_text(
        f'"{COPYRIGHT}",,,\n'
        '"HCPCS/\nCPT Code",Outpatient Hospital Services MUE Values,'
        "MUE Adjudication Indicator,MUE Rationale\n"
        "0001U,1,2 Date of Service Edit: Policy,Code Descriptor / CPT Instruction\n"
        "99213,4,3 Date of Service Edit: Clinical,Nature of Analyte\n"
    )
    additions = tmp_path / "additions.txt"
    additions.write_text(
        f'"{COPYRIGHT}"\t\t\n'
        'Column 1\tColumn 2\t"Modifier\nIndicator"\n'
        "0002M\t0468U\t1\n"
        "99213\t99214\t0\n"
    )
    deletions = tmp_path / "deletions.txt"
    deletions.write_text(
        f'"{COPYRIGHT}"\t\t\nColumn 1\tColumn 2\t"Modifier\nIndicator"\n0001U\t0029U\t1\n'
    )
    out = tmp_path / "ncci.sqlite"
    return ncci.build_db(
        mue_csv=mue_csv, ptp_additions_txt=additions, ptp_deletions_txt=deletions, out_path=out
    )


def test_ptp_lookup_matches_forward_order(db_path):
    with ncci.NCCITable(db_path) as tbl:
        r = tbl.lookup("0002M", "0468U")
        assert r.matched
        assert r.column1 == "0002M"
        assert r.modifier_indicator == 1
        assert r.allowed_with_modifier is True
        assert r.source == "addition"


def test_ptp_lookup_matches_reversed_order(db_path):
    """A claim can present the two billed codes in either order."""
    with ncci.NCCITable(db_path) as tbl:
        r = tbl.lookup("0468U", "0002M")
        assert r.matched
        assert r.column1 == "0002M"  # original column order preserved


def test_ptp_modifier_indicator_zero_never_billable(db_path):
    with ncci.NCCITable(db_path) as tbl:
        r = tbl.lookup("99213", "99214")
        assert r.matched
        assert r.modifier_indicator == 0
        assert r.allowed_with_modifier is False
        assert "may NEVER be billed together" in r.explain()


def test_ptp_no_match(db_path):
    with ncci.NCCITable(db_path) as tbl:
        r = tbl.lookup("11111", "22222")
        assert not r.matched
        assert "No NCCI PTP edit found" in r.explain()


def test_ptp_deletions_loaded_and_tagged(db_path):
    with ncci.NCCITable(db_path) as tbl:
        r = tbl.lookup("0001U", "0029U")
        assert r.matched
        assert r.source == "deletion"


def test_mue_lookup(db_path):
    with ncci.NCCITable(db_path) as tbl:
        m = tbl.mue("99213")
        assert m.mue_value == 4
        assert "4 unit(s)" in m.explain()


def test_mue_missing_code_returns_none(db_path):
    with ncci.NCCITable(db_path) as tbl:
        assert tbl.mue("00000") is None


def test_no_cpt_descriptor_text_stored(db_path):
    """Copyright boundary: the loader must never persist CPT descriptor text,
    only codes + edit flags + CMS's own rationale category labels."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM mue").fetchall()
    ptp_rows = conn.execute("SELECT * FROM ptp_edits").fetchall()
    conn.close()
    for row in rows + ptp_rows:
        for cell in row:
            if isinstance(cell, str):
                assert "copyright" not in cell.lower()


def test_lookup_and_mue_under_10ms(db_path):
    with ncci.NCCITable(db_path) as tbl:
        t0 = time.perf_counter()
        for _ in range(100):
            tbl.lookup("0002M", "0468U")
            tbl.mue("99213")
        elapsed_ms = (time.perf_counter() - t0) / 100 * 1000
    assert elapsed_ms < 10, f"avg call took {elapsed_ms:.3f}ms"
