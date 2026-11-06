import numpy as np
import pandas as pd
import pytest

from ilcd.change import dod
from ilcd.common import config, contracts, grid, io


@pytest.fixture(scope="module")
def params():
    return config.default_params()


def _ground(rng, cell, n, z, pad):
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    x = rng.uniform(xmin - pad, xmax + pad, n)
    y = rng.uniform(ymin - pad, ymax + pad, n)
    return x, y, np.full(n, z) + rng.normal(0, 0.02, n)


def test_dod_classes_and_raster(params, tmp_path):
    rng = np.random.default_rng(0)
    cell, p = "E0550N4330", params.section("change.dod")
    pad = p["pad_m"]
    xl, yl, zl = _ground(rng, cell, 2_000_000, 100.0, pad)
    xm, ym, zm = _ground(rng, cell, 3_000_000, 100.0, pad)
    xmin, ymin, *_ = grid.cell_bounds(cell)
    raised = (xm > xmin + 400) & (xm < xmin + 500) & (ym > ymin + 400) & (ym < ymin + 500)
    zm[raised] += 1.0
    leg = {
        "x": xl,
        "y": yl,
        "z": zl,
        "cls": np.full(xl.size, 2, np.uint8),
        "corrected": np.ones(xl.size, bool),
        "line_id": np.full(xl.size, "abs@1.0", object),
    }
    mod = {"x": xm, "y": ym, "z": zm, "cls": np.full(xm.size, 2, np.uint8)}
    status = pd.DataFrame(
        {
            "cell_id": [cell],
            "line_id": ["abs@1.0"],
            "n_holdout": [50],
            "bias_m": [0.01],
            "nmad_m": [0.03],
            "lod95_m": [0.07],
            "status": ["corrected"],
        }
    )
    by_line, fallback = dod.sys_sd(status, cell)
    b = dod.compute(cell, leg, mod, by_line, fallback, pad, p)
    assert b["cls"].shape == (500, 500)
    c = b["cls"]
    inner, outer = c[205:245, 205:245], c[:150, :150]  # rows from the south: y 410-490 m; a few pixels lack points
    assert np.isfinite(inner).mean() > 0.9 and (inner[np.isfinite(inner)] == dod.RAISED).all()
    assert (outer[np.isfinite(outer)] == dod.STABLE).mean() > 0.99
    assert np.nanmedian(b["lod_m"]) == pytest.approx(0.07, abs=0.02)
    transform, *_ = grid.pixel_grid(cell, p["res_m"])
    out = io.write_raster(b, transform, tmp_path / "dod.tif", "dod")
    contracts.get("dod").check_raster(out)


def test_m3c2_and_objects(params):
    from shapely.geometry import LineString

    from ilcd.change import m3c2, objects

    pytest.importorskip("py4dgeo")
    rng = np.random.default_rng(1)
    n = 60_000
    leg = np.c_[rng.uniform(0, 100, n), rng.uniform(0, 100, n), rng.normal(0, 0.02, n)]
    mod = np.c_[rng.uniform(0, 100, 2 * n), rng.uniform(0, 100, 2 * n), rng.normal(0, 0.02, 2 * n)]
    pit = (mod[:, 0] > 30) & (mod[:, 0] < 50) & (mod[:, 1] > 30) & (mod[:, 1] < 50)
    mod[pit, 2] -= 0.8  # 400 m2 of loss
    core = m3c2.thin(mod, 1.0)
    t = m3c2.run(leg, mod, core, 0.03, params.section("change.m3c2"))
    contracts.get("m3c2").check_table(io.to_arrow(t, "m3c2"))
    obj, mem = objects.find(t.assign(cell_id="E0000N0000"), "s", 1.0, [LineString([(0, 0), (0, 100)])],
                            params.section("change.objects"))
    assert len(obj) == 1 and obj.loc[0, "kind"] == "loss"
    assert obj.loc[0, "area_m2"] == pytest.approx(400, rel=0.15)
    assert obj.loc[0, "mean_dz_m"] == pytest.approx(-0.8, abs=0.1)
    assert obj.loc[0, "dist_water_m"] == pytest.approx(30, abs=2) and len(mem) == obj.loc[0, "area_m2"]
