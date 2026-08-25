"""agent_core.delivery_bridge -- SIMULATED sends while RELAY's packages/delivery
is still an empty stub. Guardrail: a simulated send must never look
indistinguishable from a real one."""

from __future__ import annotations

from agent_core import delivery_bridge


def test_send_fax_is_simulated_and_labeled():
    result = delivery_bridge.send_fax("filing-1", b"pdf-bytes", "888-610-4092")
    assert result["simulated"] is True
    assert result["vendor_id"].startswith("SIMULATED-FAX-")
    assert result["status"] == "sent"


def test_send_mail_is_simulated_and_labeled():
    result = delivery_bridge.send_mail("filing-2", b"pdf-bytes", {"name": "Test Hospital"})
    assert result["simulated"] is True
    assert result["vendor_id"].startswith("SIMULATED-LOB-")
    assert "tracking" in result


def test_add_calendar_deadline_is_simulated():
    event_id = delivery_bridge.add_calendar_deadline("Charity care due", "2026-08-01", "cite")
    assert event_id.startswith("SIMULATED-CAL-")


def test_bridge_sources_all_fallback_until_relay_ships():
    sources = delivery_bridge.bridge_sources()
    assert set(sources) == {"fax", "mail", "calendar"}
    for source in sources.values():
        assert "RELAY" in source or "SWARM fallback" in source
