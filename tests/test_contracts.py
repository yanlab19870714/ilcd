import json
from importlib import resources

import pyarrow as pa
import pytest

from ilcd.common import contracts
from ilcd.common.contracts import ContractError

OWNERS = {"data_src", "flightlines", "refs", "align", "eval", "change", "baselines", "viz_label", "config", "common"}


def test_every_spec_loads_and_is_well_formed():
    cs = contracts.all_contracts()
    assert len(cs) >= 25
    for c in cs.values():
        assert c.kind in contracts.KINDS, c.tag
        assert c.owner in OWNERS, c.tag
        assert c.status in ("draft", "stable"), c.tag
        assert c.description and c.path, c.tag
        if c.kind in ("table", "vector", "geojson"):
            names = [col.name for col in c.columns]
            assert len(names) == len(set(names)), c.tag
            assert set(c.key) <= set(names), c.tag
            for col in c.columns:
                assert col.type in contracts.TYPES, (c.tag, col.name)
        if c.kind in ("vector", "geojson"):
            assert c.geometry["type"] in ("Polygon", "MultiPolygon", "Point", "LineString"), c.tag
        if c.kind == "vector":
            assert c.columns[-1].name == "geometry" and c.columns[-1].type == "geometry", c.tag
            assert c.geometry["crs"] == "EPSG:6345", c.tag
        if c.kind == "geojson":
            assert c.geometry["crs"] == "EPSG:4326", c.tag  # RFC 7946
        if c.kind == "json":
            import jsonschema

            jsonschema.Draft202012Validator.check_schema(c.schema)
        if c.kind == "raster":
            assert c.raster["crs"] == "EPSG:6345" and c.bands, c.tag


def test_spec_files_are_plain_json():
    for f in resources.files("ilcd.schemas").iterdir():
        if f.name.endswith(".json"):
            json.loads(f.read_text())


def test_get_latest_and_tag():
    assert contracts.get("passes").tag == "passes.v1"
    assert contracts.get("passes.v1") is contracts.get("passes", 1)
    with pytest.raises(KeyError):
        contracts.get("nope")


def _passes_row(**over):
    c = contracts.get("passes")
    row = {col.name: None for col in c.columns}
    row.update(
        pass_id="W.ot_in2011_31501470_12_1#1",
        file_id="W.ot_in2011_31501470_12_1",
        delivery_id="W_Monroe",
        time_domain="wk:W_Monroe",
        t_start_s=147078.0,
        t_end_s=147501.0,
        n_points=10,
        n_ground=5,
        heading_deg=1.0,
        speed_mps=65.0,
        cx_m=1.0,
        cy_m=2.0,
        xmin_m=0.0,
        ymin_m=0.0,
        xmax_m=2.0,
        ymax_m=4.0,
        scan_min_deg=-20.0,
        scan_max_deg=20.0,
        psid_mode=175,
        psid_share=0.9,
        class12_share=0.1,
    )
    row.update(over)
    return row


def test_check_table_accepts_valid_and_reports_all_problems():
    c = contracts.get("passes")
    good = pa.Table.from_pylist([_passes_row()], schema=c.arrow_schema())
    c.check_table(good)
    bad = pa.Table.from_pylist([_passes_row(), _passes_row(n_ground=None)], schema=c.arrow_schema().with_metadata({}))
    with pytest.raises(ContractError) as e:
        c.check_table(bad)
    msg = str(e.value)
    assert "n_ground has 1 nulls" in msg and "not unique" in msg


def test_check_table_types_enums_extra_columns():
    c = contracts.get("deliveries")
    t = pa.table(
        {
            "delivery_id": ["W_Monroe"],
            "zone": ["X"],
            "folder": ["Monroe"],
            "n_files": pa.array([488], pa.int64()),
            "gps_time_type": [None],
            "gps_week": pa.array([None], pa.int32()),
            "time_domain": [None],
            "datum": ["itrf"],
            "datum_source": ["test"],
            "psid_meaning": ["subtile"],
            "extra": [1],
        }
    )
    with pytest.raises(ContractError) as e:
        c.check_table(t)
    msg = str(e.value)
    assert "unexpected columns ['extra']" in msg
    assert "n_files is int64, expected int32" in msg
    assert "zone has values ['X']" in msg


def test_nan_in_non_nullable_float_is_rejected():
    c = contracts.get("passes")
    t = pa.Table.from_pylist([_passes_row(speed_mps=float("nan"))], schema=c.arrow_schema())
    with pytest.raises(ContractError, match="NaN"):
        c.check_table(t)


def test_projected_check_only_looks_at_requested_columns():
    c = contracts.get("passes")
    t = pa.table({"pass_id": ["W.a#1"], "n_points": pa.array([3], pa.int64())})
    c.check_table(t, columns=["pass_id", "n_points"])


def test_check_json_datum():
    c = contracts.get("datum")
    hyp = dict(dx_m=0.7, dy_m=-0.83, dz_m=0.0, nmad_before_m=0.3, nmad_after_m=0.1, n_points=1000, passes=False)
    doc = dict(
        delivery_id="W_Monroe",
        decision="itrf",
        geoid="GEOID09->GEOID12B",
        max_residual_m=0.3,
        samples=[
            dict(
                cell_id="E0548N4330",
                files=["W.x"],
                surface="roofs",
                itrf={**hyp, "dx_m": 0.02, "dy_m": 0.01, "passes": True},
                nad83=hyp,
            )
        ],
    )
    c.check_json(doc)
    with pytest.raises(ContractError):
        c.check_json({**doc, "decision": "maybe"})


def test_check_geojson_site():
    c = contracts.get("site")
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"site": "a", "kind": "box", "source": "x"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            }
        ],
    }
    c.check_geojson(fc)
    fc["features"][0]["properties"]["kind"] = "circle"
    with pytest.raises(ContractError, match="outside"):
        c.check_geojson(fc)
