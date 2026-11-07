import numpy as np
import pytest

from ilcd.common import config
from ilcd.common.raster import Grid, fill_nearest, nmad
from ilcd.data_src import datum, points


@pytest.fixture(scope="module")
def params():
    return config.default_params()


def test_itrf_vs_nad83_shift_at_monroe(params):
    """The two readings of the WGS84 label differ by about 1.08 m, east +, north - (verified 2026-10-21)."""
    lon, lat = np.array([-86.53]), np.array([39.16])
    xi, yi, *_ = points.legacy_lonlat_to_project(lon, lat, "itrf", params["data_src.datum.itrf_epoch"])
    xn, yn, *_ = points.legacy_lonlat_to_project(lon, lat, "nad83", params["data_src.datum.itrf_epoch"])
    dx, dy = float((xi - xn)[0]), float((yi - yn)[0])
    assert 0.9 < np.hypot(dx, dy) < 1.25
    assert dx > 0.5 and dy < -0.6


def test_modern_frame_round_trip():
    """EPT 3857 -> project and back reproduces the cell bounds used for fetching (null datum shift both ways)."""
    from ilcd.data_src.fetch import _merc_bounds

    mx0, my0, mx1, my1 = _merc_bounds("E0548N4330", 0.0)
    x, y = points.modern_merc_to_project(np.array([mx0, mx1]), np.array([my0, my1]))
    assert abs(x[0] - 548000) < 30 and abs(y[1] - 4331000) < 30  # bbox of a slightly rotated square


def test_geoid_09_to_12b_is_cm_level(params):
    lon, lat = np.array([-86.38]), np.array([39.29])
    n09 = points.geoid_undulation(lon, lat, params["data_src.geoid.legacy_grid"])
    n12 = points.geoid_undulation(lon, lat, params["data_src.geoid.project_grid"])
    assert -34 < n09[0] < -32 and abs(n09[0] - n12[0]) < 0.05


def test_shift_search_recovers_a_known_shift(params):
    rng = np.random.default_rng(1)
    G = Grid.covering(0, 0, 200, 200, 1.0)
    xc, yc = G.centers()
    X, Y = np.meshgrid(xc, yc)
    dem = 3 * np.sin(X / 9.0) + 2 * np.cos(Y / 7.0) + 0.02 * X  # hilly synthetic terrain
    f = G.interpolator(dem)
    x, y = rng.uniform(20, 180, 20000), rng.uniform(20, 180, 20000)
    true = (0.7, -0.8)  # 2011 point at (x, y) matches the surface at (x + dx, y + dy)
    z = f(x + true[0], y + true[1]) + rng.normal(0, 0.05, x.size) - 0.1
    r = datum.shift_search(x, y, z, f, params.section("data_src.datum"))
    assert r["dx_m"] == pytest.approx(true[0], abs=0.11) and r["dy_m"] == pytest.approx(true[1], abs=0.11)
    assert r["dz_m"] == pytest.approx(-0.1, abs=0.02) and not r["passes"]
    assert r["sharpness"] > 0.05  # hilly synthetic terrain: a 1 m probe raises NMAD a lot
    assert r["nmad_after_m"] < 0.5 * r["nmad_before_m"]


def _sample(itrf_shift, nad_shift, informative=True):
    def h(d):
        return {"dx_m": d[0], "dy_m": d[1]}

    return {"informative": informative, "itrf": h(itrf_shift), "nad83": h(nad_shift)}


def test_decide_is_relative(params):
    p = params.section("data_src.datum")
    carroll = _sample((-0.7, 0.9), (0.0, 0.0))
    monroe = _sample((0.0, 0.05), (0.7, -0.83))
    line_error = _sample((-0.5, 1.4), (0.1, 0.5))  # Tippecanoe E0494N4485: nad83 still wins by 1 m
    assert datum.winner(line_error, p["min_margin_m"]) == "nad83"
    assert datum.decide([carroll, line_error], p) == "nad83"
    assert datum.decide([monroe, monroe], p) == "itrf"
    assert datum.decide([carroll, monroe], p) == "ambiguous"  # samples disagree
    assert datum.decide([carroll], p) == "ambiguous"  # too few informative samples
    assert datum.decide([carroll, _sample((0.5, 0.5), (0.4, 0.4))], p) == "ambiguous"  # no clear winner
    assert datum.decide([carroll, carroll, _sample((0, 0), (1, 1), informative=False)], p) == "nad83"


def test_raster_helpers():
    G = Grid.covering(0, 0, 4, 2, 1.0)
    assert (G.nx, G.ny) == (4, 2)
    m = G.mean([0.5, 0.5, 3.5], [0.5, 0.5, 1.5], [1.0, 3.0, 5.0])
    assert m[0, 0] == 2.0 and m[1, 3] == 5.0 and np.isnan(m[0, 1])
    assert np.isfinite(fill_nearest(m)).all()
    assert nmad([1, 1, 1, 10]) == 0.0


def test_delivery_time_type_needs_every_file_header(data_root, monkeypatch, params):
    import pandas as pd

    from ilcd.common import io, paths
    from ilcd.data_src import catalog

    monkeypatch.setattr(config, "load_delivery_overrides", lambda: {})
    lf = pd.DataFrame(
        {
            "file_id": ["W.a", "W.b", "W.c"],
            "delivery_id": ["W_Monroe", "W_Monroe", "W_Brown"],
            "other_deliveries": [[], [], []],
        }
    )
    row = {
        "sha256": "0" * 64,
        "n_points": 1,
        "point_format": 1,
        "n_invalid_time": 0,
        "gps_min_s": 0.0,
        "gps_max_s": 1.0,
        "scale_xy": 0.01,
        "scale_z": 0.01,
        "n_psid": 1,
        "class12_share": 0.0,
        "copies_identical": None,
        "fetched_utc": "2026-11-07T00:00:00+00:00",
    }
    rows = [dict(row, file_id=f, gps_time_type="week_seconds") for f in ("W.a", "W.c")]
    io.write_table(pd.DataFrame(rows), paths.catalog("legacy_headers"), "legacy_headers")
    t = {r["delivery_id"]: r for r in catalog.build_deliveries(params, lf).to_pylist()}
    assert t["W_Brown"]["gps_time_type"] == "week_seconds"
    assert t["W_Monroe"]["gps_time_type"] is None  # W.b has no header yet
