"""Reference surfaces of one cell and their observations (contracts `patches`, `obs`, `overlap_obs`).

Hard ground (old patches_v2.py, ground part): 1 m cells whose 3 m window plane is smooth and flat, mostly single
returns, free of objects above the 3DEP DTM, and unvegetated in NAIP; one 3 x 3 closing, then 4-connected regions of
at least refs.ground.min_area_m2. Patches are cut at the cell boundary (the window planes use the padded points).

Observations (old patch_scan.py / measure_patches.py): every 2011 point of refs.obs.classes on a patch is compared
with the 3DEP window plane of its 1 m cell; points farther than refs.obs.outlier_m from the cell's median residual
are dropped; each (patch, pass) with at least refs.obs.min_points points gives one observation (median, standard
error 1.2533 max(NMAD, 1 cm) / sqrt(n)). Two passes on one patch give an overlap observation.

Sloped natural ground (refs.slope) gives the observations of the horizontal-shift term: its residual to the plane
moves by slope x shift, so only gross errors (refs.slope.max_dz_m) are dropped there. Roofs and flat natural ground
are added in later steps.
"""

import numpy as np
import pandas as pd
import pyarrow as pa
from scipy import ndimage
from shapely.geometry import shape
from shapely.ops import unary_union

from ..common import contracts, crs, grid, ids, io, paths
from ..common.log import get_logger
from ..common.manifest import Step
from ..common.raster import Grid, fill_nearest, nmad, smooth
from ..data_src import ancillary, osm, points
from .planes import plane_residual, window_planes

log = get_logger(__name__)


def _ndvi_on(G: Grid, cell: str, year: int) -> np.ndarray:
    """NDVI of the cell placed on the padded grid; outside the cell and where NAIP is missing: 1 (vegetated)."""
    nd = ancillary.ndvi(cell, year)
    out = np.ones((G.ny, G.nx), np.float32)
    xmin, ymin, _, _ = grid.cell_bounds(cell)
    i0, j0 = int(round((xmin - G.x0) / G.res)), int(round((ymin - G.y0) / G.res))
    out[j0 : j0 + nd.shape[0], i0 : i0 + nd.shape[1]] = np.where(np.isfinite(nd), nd, 1.0)
    return out


def hard_ground(G: Grid, m: dict, ndvi: np.ndarray, interior: np.ndarray, p: dict):
    """(patch label grid 0..K, window planes P of 3DEP ground, DTM interpolator)."""
    g = m["cls"] == 2
    dtm = smooth(fill_nearest(G.mean(m["x"][g], m["y"][g], m["z"][g])), 0.7)
    dtm_at = G.interpolator(dtm)
    hag = m["z"] - dtm_at(m["x"], m["y"])
    P = window_planes(G, m["x"][g], m["y"][g], m["z"][g])
    with np.errstate(invalid="ignore"):
        obj = G.mean(m["x"], m["y"], (hag > p["object_hag_m"]).astype(float))
        single = G.mean(m["x"], m["y"], (m["nr"] == 1).astype(float))
        has_ground = G.count(m["x"][g], m["y"][g]) > 0
        ok = (
            (P["n"] >= p["min_points"])
            & (P["rms"] < p["max_rms_m"])
            & (obj < p["max_object_share"])
            & (single >= p["min_single_share"])
            & has_ground
            & (ndvi < p["ndvi_cut"])
            & (P["slope"] < p["max_slope_deg"])
        )
    ok = ndimage.binary_closing(ok, np.ones((3, 3), bool)) & (P["n"] >= 3) & interior
    lab, n = ndimage.label(ok)
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    big = np.flatnonzero(sizes >= p["min_area_m2"] / G.res**2)
    big = big[big > 0]
    out = np.zeros_like(lab)
    out[np.isin(lab, big)] = np.searchsorted(big, lab[np.isin(lab, big)]) + 1
    return out, P, dtm_at


def slope_patches(G: Grid, P: dict, taken: np.ndarray, interior: np.ndarray, p: dict) -> np.ndarray:
    """Sloped natural ground for the horizontal-shift term: 1 m cells whose 3DEP ground window plane is steep
    (refs.slope min..max_slope_deg) and planar (rms below max_rms_m), not already hard ground; 4-connected regions
    of at least min_area_m2. Vegetation does not matter: only ground returns are used. Labels 1..K."""
    with np.errstate(invalid="ignore"):
        ok = (
            (P["n"] >= p["min_points"])
            & (P["slope"] >= p["min_slope_deg"])
            & (P["slope"] <= p["max_slope_deg"])
            & (P["rms"] < p["max_rms_m"])
        )
    ok &= ~taken & interior
    lab, n = ndimage.label(ok)
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    big = np.flatnonzero(sizes >= p["min_area_m2"] / G.res**2)
    big = big[big > 0]
    out = np.zeros_like(lab)
    out[np.isin(lab, big)] = np.searchsorted(big, lab[np.isin(lab, big)]) + 1
    return out


def _narrow(lab: np.ndarray, k: int, res: float, p: dict) -> np.ndarray:
    """narrow[1..k] (index 0 unused): road-like shape (old patch_class.py)."""
    depth = ndimage.distance_transform_edt(lab > 0) * res
    deep = np.bincount(lab.ravel(), (depth > p["narrow_half_width_m"]).ravel().astype(float), k + 1)
    area = np.bincount(lab.ravel(), minlength=k + 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return deep / area < p["narrow_wide_share"]


def _road_share(lab: np.ndarray, G: Grid, k: int, p: dict) -> np.ndarray:
    """Share of each patch's cells within refs.ground.road_dist_m of an OSM vehicle-road centreline (old
    patch_class.road_share; footways, paths and the like do not count). Index 0 unused."""
    from rasterio import features
    from rasterio.transform import Affine

    roads = osm.layer("roads", (G.x0 - 50, G.y0 - 50, G.x0 + G.nx * G.res + 50, G.y0 + G.ny * G.res + 50))
    roads = roads[~roads["highway"].isin(p["not_road"])]
    if roads.empty:
        return np.zeros(k + 1)
    t = Affine(G.res, 0, G.x0, 0, G.res, G.y0)
    near = features.rasterize(
        ((g, 1) for g in roads.geometry.buffer(p["road_dist_m"])),
        out_shape=(G.ny, G.nx),
        transform=t,
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.bincount(lab.ravel(), near.ravel().astype(float), k + 1) / np.bincount(lab.ravel(), minlength=k + 1)


def _water_distance(polys: list, cell: str, search_m: float) -> list:
    """Distance from each patch to the nearest OSM water (body or waterway), None beyond search_m. Index 0 unused."""
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    water = osm.layer("water", (xmin - search_m, ymin - search_m, xmax + search_m, ymax + search_m))
    out = [None] * len(polys)
    if water.empty:
        return out
    geoms = water.geometry.values
    for q in range(1, len(polys)):
        d = float(geoms.distance(polys[q]).min())
        out[q] = d if d <= search_m else None
    return out


def _polygons(lab: np.ndarray, G: Grid, k: int) -> list:
    from rasterio import features
    from rasterio.transform import Affine

    t = Affine(G.res, 0, G.x0, 0, G.res, G.y0)  # row 0 = south: y grows with the row index
    geoms = [[] for _ in range(k + 1)]
    for gj, v in features.shapes(lab.astype(np.int32), mask=lab > 0, transform=t, connectivity=4):
        geoms[int(v)].append(shape(gj))
    return [unary_union(g) for g in geoms]


def assign_passes(file_idx: np.ndarray, t: np.ndarray, files: list[str], passes: dict[str, pd.DataFrame]) -> np.ndarray:
    """pass_id of every point (None outside every pass), from the files' passes tables (time ranges)."""
    out = np.full(t.size, None, object)
    for i, f in enumerate(files):
        P = passes[f].sort_values("t_start_s")
        m = file_idx == i
        k = np.searchsorted(P["t_start_s"].to_numpy(), t[m], side="right") - 1
        valid = k >= 0
        kk = np.clip(k, 0, None)
        valid &= t[m] <= P["t_end_s"].to_numpy()[kk]
        ids_ = P["pass_id"].to_numpy()[kk]
        out[np.flatnonzero(m)[valid]] = ids_[valid]
    return out


def build(cell: str, params) -> tuple[pa.Table, pa.Table, pa.Table, str | None]:
    """(patches, obs, overlap_obs, empty reason or None)."""
    pg, po = params.section("refs.ground"), params.section("refs.obs")
    pad = params["data_src.points.read_pad_m"]
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    G = Grid.covering(xmin - pad, ymin - pad, xmax + pad, ymax + pad, params["refs.grid_res_m"])
    m = points.read_modern(cell, params)
    empties = (
        contracts.get("patches").empty_table(),
        contracts.get("obs").empty_table(),
        contracts.get("overlap_obs").empty_table(),
    )
    if np.sum(m["cls"] == 2) < 100:
        return *empties, "no 3DEP ground"
    xc, yc = G.centers()
    XC, YC = np.meshgrid(xc, yc)
    interior = (XC >= xmin) & (XC < xmax) & (YC >= ymin) & (YC < ymax)
    lab, P, _ = hard_ground(G, m, _ndvi_on(G, cell, pg["ndvi_year"]), interior, pg)
    k_hard = int(lab.max())
    sl = slope_patches(G, P, lab > 0, interior, params.section("refs.slope"))
    lab = np.where(sl > 0, sl + k_hard, lab)  # labels 1..k_hard hard ground, k_hard+1..k sloped ground
    k = int(lab.max())
    if k == 0:
        return *empties, "no hard or sloped ground"

    # 2011 points on patches, residual to the 3DEP window plane
    leg = points.read_legacy(cell, params)
    files = leg["parts"]
    passes = {f: io.read_table(paths.file_dir(f) / "passes.parquet", "passes").to_pandas() for f in files}
    i, j, inside = G.index(leg["x"], leg["y"])
    pid = np.zeros(leg["x"].size, np.int64)
    pid[inside] = lab[j[inside], i[inside]]
    sel = np.isin(leg["cls"], po["classes"]) & (pid > 0)
    r = plane_residual(G, P, leg["x"][sel], leg["y"][sel], leg["z"][sel])
    ok = np.isfinite(r)
    hard = pid[sel] <= k_hard
    if (ok & hard).any():  # hard ground: points far from the cell's median residual are not ground
        ok &= ~hard | (np.abs(r - np.median(r[ok & hard])) < po["outlier_m"])
    # sloped ground: a horizontal shift moves the residual by slope x shift, so only gross errors are dropped
    ok &= hard | (np.abs(r) < params["refs.slope.max_dz_m"])
    idx = np.flatnonzero(sel)[ok]
    r = r[ok]
    pas = assign_passes(leg["part"][idx], leg["t"][idx], files, passes)
    D = pd.DataFrame(
        {
            "patch": pid[idx],
            "pass_id": pas,
            "r": r,
            "scan": leg["scan_deg"][idx],
            "t": leg["t"][idx],
            "x": leg["x"][idx],
            "y": leg["y"][idx],
        }
    ).dropna(subset=["pass_id"])

    # patches
    narrow = _narrow(lab, k, G.res, pg)
    polys = _polygons(lab, G, k)
    area = np.bincount(lab.ravel(), minlength=k + 1) * G.res**2
    lab_flat = lab.ravel()

    def per_patch(a):
        s = np.bincount(lab_flat, np.nan_to_num(a.ravel()), k + 1)
        return s / np.maximum(np.bincount(lab_flat, np.isfinite(a.ravel()).astype(float), k + 1), 1)

    slope, rough, zmean = per_patch(P["slope"]), per_patch(P["rms"]), per_patch(P["zc"])
    ga, gb = per_patch(P["a"]), per_patch(P["b"])
    gi, gj, gin = G.index(m["x"], m["y"])
    gmask = gin & (m["cls"] == 2)
    gl = np.zeros(m["x"].size, np.int64)
    gl[gmask] = lab[gj[gmask], gi[gmask]]
    n_modern = np.bincount(gl[gl > 0], minlength=k + 1)
    batch_of = {}
    for q in range(1, k + 1):
        parts = m["part"][gl == q]
        batch_of[q] = m["parts"][int(np.bincount(parts).argmax())] if parts.size else m["parts"][0]
    road = _road_share(lab, G, k, pg)
    kind = {
        q: "natural_slope" if q > k_hard else "road" if road[q] >= pg["road_share"] else "hard_other"
        for q in range(1, k + 1)
    }
    pid_of = {q: ids.patch_id(cell, kind[q], q) for q in range(1, k + 1)}
    dist_water = _water_distance(polys, cell, params["refs.screen.water_search_m"])
    pooled = D.groupby("patch")["r"].agg(["median", "size", lambda v: nmad(v)])
    pooled.columns = ["median", "n", "nmad"]
    rows = []
    for q in range(1, k + 1):
        pr = pooled.loc[q] if q in pooled.index else None
        rows.append(
            {
                "patch_id": pid_of[q],
                "kind": kind[q],
                "source": "geometry" if q > k_hard else "ndvi",
                "modern_batch": batch_of[q],
                "area_m2": float(area[q]),
                "slope_deg": float(slope[q]),
                "aspect_deg": float((np.degrees(np.arctan2(-ga[q], -gb[q])) + 360) % 360),
                "z_m": float(zmean[q]),
                "rough_m": float(rough[q]),
                "n_modern": int(n_modern[q]),
                "narrow": bool(narrow[q]),
                "road_share": float(road[q]),
                "dist_water_m": dist_water[q],
                "n_legacy": int(pr["n"]) if pr is not None else 0,
                "legacy_dz_m": float(pr["median"]) if pr is not None else None,
                "legacy_se_m": float(1.2533 * max(pr["nmad"], 0.01) / np.sqrt(pr["n"])) if pr is not None else None,
                "osm_id": None,
                "cdl_class": None,
                "excluded": False,
                "exclude_reason": None,
                "geometry": polys[q],
            }
        )
    import geopandas as gpd

    patches = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs.PROJECT_CRS)

    # observations per (patch, pass)
    o = []
    for (q, pss), v in D.groupby(["patch", "pass_id"]):
        if len(v) < po["min_points"]:
            continue
        rr = v["r"].to_numpy()
        o.append(
            {
                "patch_id": pid_of[int(q)],
                "pass_id": pss,
                "modern_batch": batch_of[int(q)],
                "dz_m": float(np.median(rr)),
                "sigma_m": float(1.2533 * max(nmad(rr), 0.01) / np.sqrt(rr.size)),
                "n_legacy": int(rr.size),
                "scan_deg": float(np.median(v["scan"])),
                "t_s": float(v["t"].mean()),
                "x_m": float(v["x"].mean()),
                "y_m": float(v["y"].mean()),
                "gx": float(ga[int(q)]),
                "gy": float(gb[int(q)]),
            }
        )
    obs = pa.Table.from_pylist(o, schema=contracts.get("obs").arrow_schema())

    # overlap observations: every pair of passes on one patch
    ov = []
    obs_df = obs.to_pandas()
    for patch, v in obs_df.groupby("patch_id"):
        v = v.sort_values("pass_id")
        for a in range(len(v)):
            for b in range(a + 1, len(v)):
                A, B = v.iloc[a], v.iloc[b]
                ov.append(
                    {
                        "patch_id": patch,
                        "pass_a": A["pass_id"],
                        "pass_b": B["pass_id"],
                        "dz_ab_m": float(A["dz_m"] - B["dz_m"]),
                        "sigma_m": float(np.hypot(A["sigma_m"], B["sigma_m"])),
                        "n_a": int(A["n_legacy"]),
                        "n_b": int(B["n_legacy"]),
                        "scan_a_deg": float(A["scan_deg"]),
                        "scan_b_deg": float(B["scan_deg"]),
                        "x_m": float((A["x_m"] + B["x_m"]) / 2),
                        "y_m": float((A["y_m"] + B["y_m"]) / 2),
                    }
                )
    overlap = pa.Table.from_pylist(ov, schema=contracts.get("overlap_obs").arrow_schema())
    return io.to_arrow(patches, "patches"), obs, overlap, None if len(o) else "no observation"


def cell_step(cell: str, params):
    out = paths.cell_dir(cell) / "refs"
    with Step("refs.cell", out_dir=out, params=params.section("refs"), unit=cell) as step:
        patches, obs, overlap, empty = build(cell, params)
        if patches.num_rows:
            step.output(io.write_table(patches, out / "patches.parquet", "patches"), "patches")
        else:
            step.output(io.write_empty_table(out / "patches.parquet", "patches"), "patches")
        step.output(io.write_table(obs, out / "obs.parquet", "obs"), "obs")
        step.output(io.write_table(overlap, out / "overlap_obs.parquet", "overlap_obs"), "overlap_obs")
        if empty:
            step.empty(empty)
    log.info(
        "cell %s: %d patches, %d obs, %d overlap obs%s",
        cell,
        patches.num_rows,
        obs.num_rows,
        overlap.num_rows,
        f" ({empty})" if empty else "",
    )
