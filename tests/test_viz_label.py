import base64
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from ilcd.common import config, contracts, io
from ilcd.common.crs import PROJECT_CRS
from ilcd.viz_label import labels, overview, page, sample, scene

X0, Y0 = 550_000.0, 4_330_000.0


@pytest.fixture(scope="module")
def params():
    return config.default_params()


def _objects(n=60, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        x, y = X0 + rng.uniform(100, 1900), Y0 + rng.uniform(100, 1900)
        s = rng.choice([6.0, 15.0, 40.0])
        rows.append(
            {
                "object_id": f"t.m3c2_dbscan.{i + 1}",
                "method": "m3c2_dbscan",
                "kind": "loss" if i % 2 else "gain",
                "area_m2": s * s,
                "volume_m3": s * s * (-0.5 if i % 2 else 0.5),
                "mean_dz_m": -0.5 if i % 2 else 0.5,
                "p90_abs_dz_m": 0.8,
                "dist_water_m": None if i == 0 else float(rng.uniform(0, 300)),
                "slope_deg": 10.0,
                "geometry": box(x, y, x + s, y + s),
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=PROJECT_CRS)


def test_sample_strata_and_squares(params):
    obj = _objects()
    rv, sc = params.section("viz_label.review"), params.section("viz_label.scene")
    st = sample.strata(obj, rv)
    assert st.iloc[0].endswith("/far")  # no water distance
    assert set(st.str.split("/").str[1]) <= {"0-100", "100-1000", ">=1000"}
    s = sample.choose(obj, rv, sc)
    assert len(s) == sum(min(rv["per_stratum"], n) for n in st.value_counts())
    assert list(s["scene_id"]) == [f"s{i:03d}" for i in range(1, len(s) + 1)]
    pd.testing.assert_frame_equal(s.drop(columns="geometry"), sample.choose(obj, rv, sc).drop(columns="geometry"))
    for r in s.itertuples():
        o = obj.set_index("object_id").geometry[r.object_id]
        assert r.geometry.contains(o) and r.size_m >= sc["min_size_m"] and r.cells
    big = box(X0, Y0, X0 + 300, Y0 + 10)
    g, size = sample.square(big, sc)
    assert size == 360.0 and g.contains(big)
    site = (X0, Y0, X0 + 1000, Y0 + 1000)
    g, size = sample.square(box(X0 + 5, Y0 + 990, X0 + 15, Y0 + 995), sc, site)  # at the north-west corner
    assert size == 150.0 and g.bounds == (X0, Y0 + 850, X0 + 150, Y0 + 1000)
    g, _ = sample.square(big, sc, (X0, Y0, X0 + 200, Y0 + 2000))  # site narrower than the square: centred
    assert g.bounds[0] == X0 - 80
    gdf = gpd.GeoDataFrame(s, geometry="geometry", crs=PROJECT_CRS)
    io.to_arrow(gdf, "scenes")  # matches the contract


def test_years():
    assert scene.batch_year("IN_Statewide_Opt2_B4_2017") == 2017
    assert scene.naip_year(2017, [2016, 2018, 2020]) == 2018  # tie: the later year
    assert scene.naip_year(2019, [2016, 2018, 2020, 2022]) == 2020
    with pytest.raises(ValueError):
        scene.batch_year("no_year_here")


def test_scene_build(params):
    rng = np.random.default_rng(0)
    sc = params.section("viz_label.scene")
    row = pd.Series(
        {"scene_id": "s001", "object_id": "t.m3c2_dbscan.1", "stratum": "gain/0-100/near", "size_m": 150.0,
         "geometry": box(X0, Y0, X0 + 150, Y0 + 150)}
    )  # fmt: skip
    row = type("Row", (), row.to_dict())

    def pts(n, z):
        return {"x": X0 + rng.uniform(0, 150, n), "y": Y0 + rng.uniform(0, 150, n), "z": z + rng.normal(0, 0.05, n),
                "cls": np.where(rng.random(n) < 0.8, 2, 5).astype(np.uint8)}  # fmt: skip

    leg, mod = pts(60_000, 100.0), pts(80_000, 100.2)
    cores = pd.DataFrame(
        {"core_id": np.arange(500), "x_m": X0 + rng.uniform(0, 150, 500), "y_m": Y0 + rng.uniform(0, 150, 500),
         "z_m": 100.2, "d_m": np.r_[np.full(499, 0.4), np.nan], "lod_m": 0.1,
         "object_id": np.where(np.arange(500) < 100, "t.m3c2_dbscan.1", None)}
    )  # fmt: skip
    obj = pd.DataFrame({"object_id": ["t.m3c2_dbscan.1", "t.m3c2_dbscan.7"]})
    d = scene.build(row, leg, mod, cores, obj, {"leg": (b"jpg1", "a"), "mod": (b"jpg2", "b")}, sc)
    p = np.frombuffer(base64.b64decode(d["leg"]), "<i2").reshape(-1, 3)
    assert len(p) == sc["points"] and p[:, :2].min() >= 0 and p[:, :2].max() <= 150 * scene.Q
    t = {k: np.frombuffer(base64.b64decode(d["t_" + k]["z"]), "<i2") for k in ("leg", "mod")}
    assert d["t_mod"]["n"] ** 2 == t["mod"].size and (t["mod"] != scene.NA).mean() > 0.95
    med = {k: np.median(v[v != scene.NA]) / scene.Q for k, v in t.items()}
    assert med["mod"] - med["leg"] == pytest.approx(0.2, abs=0.03)
    ob = np.frombuffer(base64.b64decode(d["core_obj"]), "<u2")
    assert (ob == 1).sum() == 100 and set(np.unique(ob)) == {0, 1}
    ok = np.frombuffer(base64.b64decode(d["core_ok"]), "u1")
    assert ok.sum() == 499
    assert d["objects"] == ["t.m3c2_dbscan.1", "t.m3c2_dbscan.7"]
    json.dumps(d, allow_nan=False)


def test_overview_colours():
    acc = overview.Accumulator((X0, Y0, X0 + 100, Y0 + 60), 4.0)
    assert (acc.w, acc.h) == (25, 15)
    x, y = np.array([X0 + 2, X0 + 50, X0 + 98]), np.array([Y0 + 2, Y0 + 30, Y0 + 58])
    acc.add(x, y, np.array([100.0, 101.0, 102.0]), np.array([-2.0, 0.05, 2.0]), np.array([True, False, True]))
    a = acc.rgba(1.0)
    assert a.shape == (15, 25, 4)
    sw, ne, mid = a[-1, 0], a[0, -1], a[7, 12]  # row 0 = north
    assert tuple(sw[:3]) == tuple(overview.LOSS) and tuple(ne[:3]) == tuple(overview.GAIN)
    assert mid[0] == mid[1] == mid[2] and mid[3] == 255
    assert a[7, 3, 3] == 0  # no core point


def test_page_render(params):
    obj = _objects(8)
    sc = sample.choose(obj, params.section("viz_label.review"), params.section("viz_label.scene"))
    ov = {"png": "overview.png", "xmin": X0, "ymin": Y0, "width": 500, "height": 500, "res_m": 4.0, "ramp_m": 1.0}
    data = page.site_data({"site": "t", "scope": "region:x"}, obj, sc, ov, {"x": 1}, ["no_change"])
    r = data["objects"][0][-1][0]
    assert len(r) == 8 and min(r) >= 0  # 4 corners, half metres from the overview corner
    assert sum(o[8] is not None for o in data["objects"]) == len(sc)
    html = page.render(data)
    assert "__DATA__" not in html and '"site":"t"' in html and html.count("</script>") == 3


def test_labels_import_and_merge():
    obj = _objects(5)
    doc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {
            "object_id": "t.m3c2_dbscan.2", "class": "bank_retreat", "annotator": " Doug ", "tool": "web3d",
            "created_utc": "2026-11-09T00:00:00Z", "blind": False, "note": None}}]}  # fmt: skip
    new = labels.from_page(doc, obj)
    assert new[0]["properties"]["label_id"] == "t.m3c2_dbscan.2:Doug" and new[0]["geometry"]["type"] == "Polygon"
    lon = new[0]["geometry"]["coordinates"][0][0][0]
    assert -90 < lon < -80
    merged = labels.merge(None, new)
    contracts.get("labels").check_geojson(merged)
    again = json.loads(json.dumps(doc))
    again["features"][0]["properties"]["class"] = "no_change"
    merged = labels.merge(merged, labels.from_page(again, obj))
    assert len(merged["features"]) == 1 and merged["features"][0]["properties"]["class"] == "no_change"
    bad = json.loads(json.dumps(doc))
    bad["features"][0]["properties"].update(object_id="nope", **{"class": "x"})
    with pytest.raises(labels.LabelError):
        labels.from_page(bad, obj)
