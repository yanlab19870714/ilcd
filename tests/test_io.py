import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ilcd.common import contracts, io
from ilcd.common.contracts import ContractError


def _lines_table(n=2):
    return pa.table(
        {
            "line_id": [f"abs@{i}.0" for i in range(n)],
            "block_id": ["abs@0#B"] * n,
            "time_domain": ["abs"] * n,
            "t_start_s": [float(i) for i in range(n)],
            "t_end_s": [i + 1.0 for i in range(n)],
            "n_passes": [1] * n,
            "heading_deg": [90.0] * n,
            "x0_m": [0.0] * n,
            "y0_m": [0.0] * n,
            "length_m": [10.0] * n,
            "n_points": [5] * n,
        }
    )


def test_write_read_round_trip_casts_and_tags(tmp_path):
    p = io.write_table(_lines_table(), tmp_path / "a" / "lines.parquet", "lines")
    assert pq.read_schema(p).metadata[contracts.METADATA_KEY] == b"lines.v1"
    t = io.read_table(p, "lines")
    assert t.schema.field("heading_deg").type == pa.float32()  # cast from float64 on write
    assert t.schema.field("n_passes").type == pa.int32()
    assert not list(p.parent.glob(".*.tmp"))


def test_write_rejects_and_leaves_no_file(tmp_path):
    bad = _lines_table().drop_columns(["n_points"])
    with pytest.raises(ContractError, match="missing columns"):
        io.write_table(bad, tmp_path / "lines.parquet", "lines")
    assert not (tmp_path / "lines.parquet").exists()
    dup = pa.concat_tables([_lines_table(1), _lines_table(1)])
    with pytest.raises(ContractError, match="not unique"):
        io.write_table(dup, tmp_path / "lines.parquet", "lines")


def test_read_refuses_other_contract(tmp_path):
    p = io.write_table(_lines_table(), tmp_path / "lines.parquet", "lines")
    with pytest.raises(ContractError, match="written as lines.v1"):
        io.read_table(p, "passes")


def test_empty_outputs(tmp_path):
    p = io.write_empty_table(tmp_path / "obs.parquet", "obs")
    assert io.read_table(p, "obs").num_rows == 0
    v = io.write_empty_table(tmp_path / "patches.parquet", "patches")
    assert io.read_table(v, "patches").num_rows == 0


def test_vector_round_trip_with_geopandas(tmp_path):
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {"batch": ["IN_Statewide_Opt2_B2_2017"], "ept_url": ["https://x/ept.json"], "n_points": [10]},
        geometry=[box(500000, 4300000, 510000, 4310000)],
        crs="EPSG:6345",
    )
    p = io.write_table(gdf, tmp_path / "modern_batches.parquet", "modern_batches")
    back = io.read_vector(p, "modern_batches")
    assert back.crs.to_epsg() == 6345 and back.geometry.iloc[0].area == pytest.approx(1e8)
    with pytest.raises(ContractError, match="crs"):
        io.write_table(gdf.to_crs(4326), tmp_path / "bad.parquet", "modern_batches")


def test_json_write_validates(tmp_path):
    doc = {
        "scope": "region:carroll",
        "method": "ilcd_two_stage",
        "fold_unit": "cell",
        "segment_unit": "cell",
        "n_folds": 5,
        "test_kinds": ["road", "hard_other"],
        "stages": [{"name": "stage1", "rms_m": 0.0355, "nmad_m": 0.03, "n": 1000}],
    }
    p = io.write_json(doc, tmp_path / "cv.json", "cv")
    assert io.read_json(p, "cv") == doc
    with pytest.raises(ContractError):
        io.write_json({**doc, "n_folds": "five"}, tmp_path / "cv2.json", "cv")
    with pytest.raises(ValueError):  # NaN is not JSON
        io.write_json({**doc, "stages": [{**doc["stages"][0], "rms_m": float("nan")}]}, tmp_path / "cv3.json", "cv")
    assert json.loads(p.read_text())["n_folds"] == 5


def test_written_files_follow_the_umask(tmp_path):
    import os
    import stat

    old = os.umask(0o002)
    try:
        p = io.write_json(
            {
                "scope": "region:x",
                "method": "m",
                "fold_unit": "cell",
                "segment_unit": "cell",
                "n_folds": 5,
                "test_kinds": ["road"],
                "stages": [],
            },
            tmp_path / "cv.json",
            "cv",
        )
    finally:
        os.umask(old)
    assert stat.S_IMODE(p.stat().st_mode) == 0o664  # group-readable, as the shared store needs
