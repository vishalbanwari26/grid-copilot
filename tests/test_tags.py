"""Tag decoder tests: HAI tags decode to plain descriptions, others are skipped."""

from __future__ import annotations

from grid_copilot.tags import decode_tag


def test_decodes_hai_instrument_tags():
    assert decode_tag("P1_PCV01Z") == "boiler pressure-control-valve position"
    assert decode_tag("P1_PCV01D") == "boiler pressure-control-valve command"
    assert decode_tag("P1_PIT01") == "boiler pressure transmitter"
    assert decode_tag("P2_VXT02") == "turbine vibration sensor"
    assert decode_tag("P3_LCV01D").startswith("water-treatment level-control-valve")


def test_unknown_tags_return_empty():
    # The synthetic generator's signal names must not be annotated.
    assert decode_tag("bearing_temp_c") == ""
    assert decode_tag("frequency_hz") == ""
