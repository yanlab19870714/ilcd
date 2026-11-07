"""Change objects of a site from its M3C2 core points (contracts `objects`, `object_members`; old m3c2_objects.py).

Significant core points (|d| > lod and |d| >= change.objects.min_magnitude_m) are split by sign and clustered in
plan view with DBSCAN (dbscan_eps_m, dbscan_min_samples) over the whole site, so objects cross cell borders;
clusters below min_area_m2 (core points x core cell area) are dropped. Objects are numbered by decreasing area.
Per object: area, signed volume (distance x core cell area), mean and 90th-percentile |distance|, median slope
(from the normal), distance from its outline to the nearest OSM water, and its outline (its core cells, rasterised
and vectorised).
"""

import numpy as np
import pandas as pd
from shapely.geometry import shape
from shapely.ops import unary_union

METHOD = "m3c2_dbscan"


def candidates(t: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Rows that can belong to an object (significant, |d| >= min_magnitude_m). A site has ~10^8 core points;
    keep only these while reading its cells."""
    return t[t["sig"].to_numpy(bool) & (np.abs(t["d_m"].to_numpy(float)) >= p["min_magnitude_m"])]


def outline(x: np.ndarray, y: np.ndarray, res: float):
    """Union of the res x res core cells of the points, by rasterising them on their own grid."""
    from rasterio import features
    from rasterio.transform import Affine

    x0, y0 = np.floor(x.min() / res) * res, np.floor(y.min() / res) * res
    i, j = np.floor((x - x0) / res).astype(int), np.floor((y - y0) / res).astype(int)
    a = np.zeros((j.max() + 1, i.max() + 1), np.uint8)
    a[j, i] = 1
    t = Affine(res, 0, x0, 0, res, y0)  # row 0 = south
    return unary_union([shape(g) for g, _ in features.shapes(a, mask=a > 0, transform=t, connectivity=4)])


def find(cores: pd.DataFrame, site: str, core_res: float, water, p: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """cores: m3c2 rows of the site's cells with a cell_id column. water: shapely geometries of OSM water (may be
    empty). Returns (objects with a shapely `geometry` column, members). p = params.section('change.objects')."""
    from sklearn.cluster import DBSCAN

    cores = candidates(cores, p).reset_index(drop=True)
    d = cores["d_m"].to_numpy(float)
    sig = np.ones(len(cores), bool)
    cell_area = core_res**2
    found = []
    for kind, sel in (("loss", sig & (d < 0)), ("gain", sig & (d > 0))):
        idx = np.flatnonzero(sel)
        if idx.size < p["dbscan_min_samples"]:
            continue
        xy = cores[["x_m", "y_m"]].to_numpy()[idx]
        lab = DBSCAN(eps=p["dbscan_eps_m"], min_samples=p["dbscan_min_samples"]).fit_predict(xy)
        for c in np.unique(lab[lab >= 0]):
            k = idx[lab == c]
            if k.size * cell_area >= p["min_area_m2"]:
                found.append((kind, k))
    found.sort(key=lambda f: -f[1].size)
    water = list(water)
    rows, members = [], []
    for n, (kind, k) in enumerate(found, start=1):
        oid = f"{site}.{METHOD}.{n}"
        dk = d[k]
        x, y = cores["x_m"].to_numpy()[k], cores["y_m"].to_numpy()[k]
        geom = outline(x, y, core_res)
        slope = np.degrees(np.arccos(np.clip(cores["normal_z"].to_numpy(float)[k], 0, 1)))
        rows.append(
            {
                "object_id": oid,
                "method": METHOD,
                "kind": kind,
                "area_m2": float(k.size * cell_area),
                "volume_m3": float(dk.sum() * cell_area),
                "mean_dz_m": float(dk.mean()),
                "p90_abs_dz_m": float(np.percentile(np.abs(dk), 90)),
                "dist_water_m": float(min(geom.distance(w) for w in water)) if water else None,
                "slope_deg": float(np.median(slope)),
                "geometry": geom,
            }
        )
        members.append(
            pd.DataFrame(
                {"cell_id": cores["cell_id"].to_numpy()[k], "core_id": cores["core_id"].to_numpy()[k], "object_id": oid}
            )
        )
    cols = [
        "object_id",
        "method",
        "kind",
        "area_m2",
        "volume_m3",
        "mean_dz_m",
        "p90_abs_dz_m",
        "dist_water_m",
        "slope_deg",
        "geometry",
    ]
    obj = pd.DataFrame(rows, columns=cols)
    mem = (
        pd.concat(members, ignore_index=True) if members else pd.DataFrame(columns=["cell_id", "core_id", "object_id"])
    )
    return obj, mem
