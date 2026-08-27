"""agent_core.agents.reader -- persona 5 WO8, `_scrub_ungrounded`.

Live on ef-2026-0006 (PROOF's deliberately-unparseable-bill fixture), the
extraction model, given a corrupted/truncated PDF it could not actually
read, fabricated a plausible-looking sentinel for several fields instead of
returning null/omitting them: `hospital_ein: "00-0000000"`,
`hospital_ccn: "000000"`, `provider_name: "Unknown"`, and
`service_date`/`first_statement_date`/`validation_notice_date`: "1970-01-01"
(Unix epoch). `_scrub_ungrounded` is the deterministic, model-independent
guarantee that no such value ever reaches `documents.extracted` or the
`bill` it gets merged into -- it is a pure function (no LLM, no network),
so these are plain unit tests.
"""

from __future__ import annotations

from agent_core.agents import reader


def test_scrubs_the_exact_ef_2026_0006_fabrications():
    extraction = {
        "hospital_ein": "00-0000000",
        "hospital_ccn": "000000",
        "provider_name": "Unknown",
        "service_date": "1970-01-01",
        "first_statement_date": "1970-01-01",
        "validation_notice_date": "1970-01-01",
        "amount_cents": 0,
    }
    cleaned, scrubbed = reader._scrub_ungrounded(extraction)
    assert set(scrubbed) == {
        "hospital_ein",
        "hospital_ccn",
        "provider_name",
        "service_date",
        "first_statement_date",
        "validation_notice_date",
    }
    for field in scrubbed:
        assert field not in cleaned
    # amount_cents is untouched here -- 0 is not this function's concern
    # (agent_core.factmerge already filters non-positive ints).
    assert cleaned["amount_cents"] == 0


def test_a_real_ein_and_recent_dates_survive_untouched():
    extraction = {
        "hospital_ein": "36-2169147",  # a real seeded EIN (Advocate)
        "provider_name": "Advocate Christ Medical Center",
        "service_date": "2026-06-01",
        "first_statement_date": "2026-06-10",
    }
    cleaned, scrubbed = reader._scrub_ungrounded(extraction)
    assert scrubbed == []
    assert cleaned == extraction


def test_placeholder_name_variants_are_all_scrubbed():
    for placeholder in ("Unknown", "N/A", "n/a", "None", "Not Available", "TBD", "  unknown  "):
        cleaned, scrubbed = reader._scrub_ungrounded({"provider_name": placeholder})
        assert scrubbed == ["provider_name"], placeholder
        assert "provider_name" not in cleaned


def test_extraction_error_dict_passes_through_unscrubbed():
    """`{"_extraction_error": ...}` (genai_client.gemini_extract_json's own
    failure sentinel) must not be treated as extracted fields."""
    extraction = {"_extraction_error": "invalid JSON after retry"}
    cleaned, scrubbed = reader._scrub_ungrounded(extraction)
    assert cleaned == extraction
    assert scrubbed == []


def test_a_real_old_historical_date_would_still_be_scrubbed_by_design():
    """The floor is deliberately generous (2000-01-01, matching
    rules.deadlines' own `_MIN_PLAUSIBLE_BASIS_DATE`) -- no genuine bill in
    this product's world predates it, so this is documenting the choice, not
    a false-positive risk."""
    cleaned, scrubbed = reader._scrub_ungrounded({"service_date": "1999-12-31"})
    assert scrubbed == ["service_date"]
    cleaned, scrubbed = reader._scrub_ungrounded({"service_date": "2000-01-01"})
    assert scrubbed == []
    assert cleaned["service_date"] == "2000-01-01"


def test_non_dict_or_malformed_extraction_is_left_alone():
    assert reader._scrub_ungrounded(None) == (None, [])
    assert reader._scrub_ungrounded({}) == ({}, [])
