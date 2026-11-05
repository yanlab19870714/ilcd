import pytest

from ilcd.common import ids


def test_delivery_and_file_round_trip():
    assert ids.delivery_id("West_IN", "Monroe") == "W_Monroe"
    assert ids.delivery_id("East_IN", "IN_E_2012") == "E_IN_E_2012"
    assert ids.parse_delivery("E_St_Joseph") == ("East_IN", "St_Joseph")
    f = ids.file_id("West_IN", "ot_in2011_31501470_12_1.laz")
    assert f == "W.ot_in2011_31501470_12_1"
    assert ids.parse_file(f) == ("West_IN", "ot_in2011_31501470_12_1.laz")
    with pytest.raises(ValueError):
        ids.file_id("North_IN", "x.laz")


def test_pass():
    p = ids.pass_id("E.ot_in2012_04152060_12", 3)
    assert p == "E.ot_in2012_04152060_12#3"
    assert ids.parse_pass(p) == ("E.ot_in2012_04152060_12", 3)
    for bad in (0, -1, 1.5):
        with pytest.raises(ValueError):
            ids.pass_id("E.ot_in2012_04152060_12", bad)
    with pytest.raises(ValueError):
        ids.parse_pass("E.ot_in2012_04152060_12#0")


def test_time_domain_and_lines():
    assert ids.time_domain("adjusted_standard", "W_Carroll", None) == "abs"
    assert ids.time_domain("week_seconds", "W_Monroe", None) == "wk:W_Monroe"
    assert ids.time_domain("week_seconds", "W_Monroe", 1627) == "abs"
    line = ids.line_id("abs", -15390699.44)
    assert line == "abs@-15390699.4"
    assert ids.parse_line(line) == ("abs", -15390699.4)
    assert ids.parse_line(ids.line_id("wk:W_Monroe", 147078.04)) == ("wk:W_Monroe", 147078.0)
    assert ids.block_id("abs", -15390699.4) == "abs@-15390700#B"  # floor, also for negative times
    assert ids.parse_block("wk:W_Monroe@147078#B") == ("wk:W_Monroe", 147078)
    with pytest.raises(ValueError):
        ids.line_id("abs", float("nan"))


def test_cells_and_patches():
    assert ids.cell_id(548, 4330) == "E0548N4330"
    assert ids.parse_cell("E0548N4330") == (548, 4330)
    p = ids.patch_id("E0548N4330", "roof_flat", 17)
    assert p == "E0548N4330.F00017"
    assert ids.parse_patch(p) == ("E0548N4330", "roof_flat", 17)
    assert len(set(ids.PATCH_KINDS.values())) == len(ids.PATCH_KINDS)
    with pytest.raises(ValueError):
        ids.patch_id("E0548N4330", "lawn", 1)


def test_scopes():
    assert ids.scope("monroe") == "region:monroe"
    assert ids.scope(None) == "statewide"
    assert ids.parse_scope("region:monroe") == "monroe"
    assert ids.parse_scope("statewide") is None
    for bad in ("monroe", "region:Monroe", "region:"):
        with pytest.raises(ValueError):
            ids.parse_scope(bad)


def test_patch_kinds_match_contract():
    from ilcd.common import contracts

    assert set(ids.PATCH_KINDS) == set(contracts.get("patches").column("kind").enum)
