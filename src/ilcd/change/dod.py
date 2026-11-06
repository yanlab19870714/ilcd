"""DEM of difference of one cell (contract `dod`; docs/design.md section 7.1).

2011 ground corrected by the scope's model (align.method.apply) and 3DEP ground of the cell's primary batch brought
to the model's reference batch (align.method.to_reference_batch), averaged on the 2 m cell grid. Points are read
with a pad so that horizontally shifted points and the water gap test see past the cell edge.
"""

import numpy as np
import pandas as pd
from scipy import ndimage

from ..common import grid
from ..common.raster import Grid

GROUND, BUILDING, WATER = 2, 6, 9
STABLE, LOWERED, RAISED, LOST_TO_WATER, LAND_GAINED, MASKED = 0, 1, 2, 3, 4, 9


def sys_sd(status: pd.DataFrame, cell: str) -> tuple[dict, float]:
    """(line_id -> systematic SD in this cell, fallback SD for lines without a usable row): lod95 / 1.96; no_data and
    missing lines use the median lod95 of the scope's corrected rows."""
    ok = status["status"].isin(["corrected", "stage1_only"])
    fallback = float(np.median(status.loc[ok, "lod95_m"])) / 1.96 if ok.any() else float("nan")
    rows = status[(status["cell_id"] == cell) & (status["status"] != "no_data")]
    return dict(zip(rows["line_id"], rows["lod95_m"] / 1.96, strict=True)), fallback


def _std(g: Grid, x, y, z, mean) -> np.ndarray:
    i, j, m = g.index(x, y)
    k = j[m] * g.nx + i[m]
    d2 = (np.asarray(z, float)[m] - mean.ravel()[k]) ** 2
    n = np.bincount(k, minlength=g.nx * g.ny)
    s = np.bincount(k, weights=d2, minlength=g.nx * g.ny)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.sqrt(s / np.maximum(n - 1, 1)).reshape(g.ny, g.nx)


def compute(cell: str, leg: dict, mod: dict, sys_by_line: dict, sys_fallback: float, pad_m: float, p: dict) -> dict:
    """Bands of the contract `dod`, south-up. leg: corrected 2011 points (with line_id, corrected); mod: 3DEP points in
    the reference frame. p = params.section('change.dod')."""
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    res = p["res_m"]
    g = Grid.covering(xmin - pad_m, ymin - pad_m, xmax + pad_m, ymax + pad_m, res)
    lg = (leg["cls"] == GROUND) & leg["corrected"]
    mg = mod["cls"] == GROUND
    n_l = g.count(leg["x"][lg], leg["y"][lg])
    n_m = g.count(mod["x"][mg], mod["y"][mg])
    z_l = g.mean(leg["x"][lg], leg["y"][lg], leg["z"][lg])
    z_m = g.mean(mod["x"][mg], mod["y"][mg], mod["z"][mg])
    s_l = _std(g, leg["x"][lg], leg["y"][lg], leg["z"][lg], z_l)
    s_m = _std(g, mod["x"][mg], mod["y"][mg], mod["z"][mg], z_m)
    lines = leg["line_id"][lg]
    s_line = np.array([sys_by_line.get(v, sys_fallback) for v in lines]) if lines.size else np.empty(0)
    i, j, m = g.index(leg["x"][lg], leg["y"][lg])
    s2 = np.bincount(j[m] * g.nx + i[m], weights=s_line[m] ** 2, minlength=g.nx * g.ny).reshape(g.ny, g.nx)
    with np.errstate(invalid="ignore", divide="ignore"):
        s_sys2 = s2 / n_l
        lod = p["lod_z"] * np.sqrt(s_l**2 / n_l + s_m**2 / n_m + s_sys2)
    dz = z_m - z_l
    k = p["min_points"]
    has_l, has_m = n_l >= k, n_m >= k
    water_l = g.count(leg["x"][leg["cls"] == WATER], leg["y"][leg["cls"] == WATER]) > 0
    no_ground_m = ndimage.distance_transform_edt(n_m == 0) * res > p["water_gap_m"]
    water_m = (g.count(mod["x"][mod["cls"] == WATER], mod["y"][mod["cls"] == WATER]) > 0) | no_ground_m
    building = (g.count(leg["x"][leg["cls"] == BUILDING], leg["y"][leg["cls"] == BUILDING]) > 0) | (
        g.count(mod["x"][mod["cls"] == BUILDING], mod["y"][mod["cls"] == BUILDING]) > 0
    )
    cls = np.full(dz.shape, np.nan)
    both = has_l & has_m
    cls[both] = STABLE
    cls[both & (np.abs(dz) > lod) & (dz < 0)] = LOWERED
    cls[both & (np.abs(dz) > lod) & (dz > 0)] = RAISED
    cls[has_l & ~has_m & water_m] = LOST_TO_WATER
    cls[water_l & ~has_l & has_m] = LAND_GAINED
    cls[building | (water_l & water_m & ~has_l & ~has_m)] = MASKED
    p0, n = int(round(pad_m / res)), int(round(grid.CELL_M / res))

    def keep(a):  # crop the pad
        return a[p0 : p0 + n, p0 : p0 + n]

    valid = both
    return {
        "z_legacy_m": keep(np.where(has_l, z_l, np.nan)),
        "z_modern_m": keep(np.where(has_m, z_m, np.nan)),
        "dz_m": keep(np.where(valid, dz, np.nan)),
        "lod_m": keep(np.where(valid, lod, np.nan)),
        "n_legacy": keep(n_l.astype(float)),
        "n_modern": keep(n_m.astype(float)),
        "cls": keep(cls),
    }
