"""Datum decision per delivery (docs/design.md section 6.4).

For each sample cell, the 2011 ground points are read under both hypotheses (itrf, nad83) and compared with a 1 m
3DEP ground surface. A two-stage grid search finds the horizontal shift that minimises the NMAD of the height
differences (old pair_diagnostics.main). The right hypothesis needs no shift.

Sample cells are spread over the delivery: candidate cells (fully covered by its files) are split into
data_src.datum.n_samples spatial clusters; in each cluster, cells are tried in random (seeded) order until one is
informative: under both hypotheses the NMAD minimum is sharp, i.e. moving data_src.datum.probe_m away from the best
shift in x or y raises the NMAD by at least min_sharpness. In flat cells the minimum is too shallow to locate.
"""

import numpy as np
from shapely.geometry import box
from shapely.ops import unary_union

from ..common import grid, io, paths
from ..common.log import get_logger
from ..common.manifest import Step
from ..common.raster import Grid, fill_nearest, nmad, smooth
from . import catalog, fetch, points

log = get_logger(__name__)
HYPOTHESES = ("itrf", "nad83")
MAX_TRIES = 4


def candidate_cells(delivery: str) -> tuple[list[str], np.ndarray]:
    lf = catalog.read_legacy_files()
    mine = lf[lf["delivery_id"] == delivery]
    if mine.empty:
        raise KeyError(f"no files with canonical delivery {delivery}")
    cover = unary_union(list(mine.geometry))
    cells = [c for c in grid.cells_for_geometry(cover) if cover.contains(box(*grid.cell_bounds(c)))]
    xy = np.array([[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] for b in map(grid.cell_bounds, cells)])
    return cells, xy


def _clusters(xy: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    from sklearn.cluster import KMeans

    k = min(k, len(xy))
    return KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(xy)


def shift_search(lx, ly, lz, surface, p) -> dict:
    """Best horizontal shift of 2011 ground points against a 3DEP surface f(x, y)."""

    def resid(dx, dy):
        return lz - surface(lx + dx, ly + dy)

    r0 = resid(0.0, 0.0)
    best = (np.inf, 0.0, 0.0)
    for step, span, centre in (
        (p["coarse_step_m"], p["coarse_span_m"], (0.0, 0.0)),
        (p["fine_step_m"], p["fine_span_m"], None),
    ):
        cx, cy = centre if centre is not None else best[1:]
        for dx in np.arange(cx - span, cx + span + 1e-9, step):
            for dy in np.arange(cy - span, cy + span + 1e-9, step):
                v = nmad(resid(dx, dy))
                if v < best[0]:
                    best = (v, float(dx), float(dy))
    nm1, dx, dy = best
    # how well the minimum is defined: smallest relative NMAD rise one probe step away in x or y
    probe = p["probe_m"]
    rise = [nmad(resid(dx + ex * probe, dy + ey * probe)) / nm1 - 1 for ex, ey in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    return {
        "dx_m": round(dx, 3),
        "dy_m": round(dy, 3),
        "dz_m": round(float(np.nanmedian(resid(dx, dy))), 4),
        "nmad_before_m": round(nmad(r0), 4),
        "nmad_after_m": round(nm1, 4),
        "n_points": int(lz.size),
        "passes": bool(np.hypot(dx, dy) < p["max_residual_m"]),
        "sharpness": round(float(min(rise)), 4),
    }


def test_cell(cell: str, params) -> dict | None:
    """One sample; None when the cell has too little 3DEP ground to compare (lake, quarry pit, outside 3DEP)."""
    p = params.section("data_src.datum")
    files = points.legacy_files_for(cell, params)
    for f in files:
        fetch.fetch_legacy(f)
    fetch.fetch_modern_step(cell, params)
    m = points.read_modern(cell, params)
    g = m["cls"] == 2
    if g.sum() < p["min_modern_ground"]:
        log.info("datum sample %s skipped: %d 3DEP ground points", cell, int(g.sum()))
        return None
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    pad = params["data_src.points.read_pad_m"]
    G = Grid.covering(xmin - pad, ymin - pad, xmax + pad, ymax + pad, 1.0)
    dem = G.mean(m["x"][g], m["y"][g], m["z"][g])
    holes = G.interpolator((~np.isfinite(dem)).astype(float), fill_value=1.0)
    surface = G.interpolator(smooth(fill_nearest(dem), 0.7))
    rng = np.random.default_rng(0)
    out = {"cell_id": cell, "files": files, "surface": "ground"}
    for h in HYPOTHESES:
        parts = [points.read_legacy_file(f, params, datum=h, bounds=(xmin, ymin, xmax, ymax)) for f in files]
        x = np.concatenate([q["x"] for q in parts])
        y = np.concatenate([q["y"] for q in parts])
        z = np.concatenate([q["z"] for q in parts])
        c = np.concatenate([q["cls"] for q in parts])
        k = (c == 2) & (x > xmin + 5) & (x < xmax - 5) & (y > ymin + 5) & (y < ymax - 5)
        k &= holes(x, y) < 0.5
        idx = np.flatnonzero(k)
        if idx.size > p["max_points"]:
            idx = rng.choice(idx, p["max_points"], replace=False)
        out[h] = shift_search(x[idx], y[idx], z[idx], surface, p)
    out["informative"] = bool(min(out[h]["sharpness"] for h in HYPOTHESES) >= p["min_sharpness"])
    log.info(
        "datum sample %s: itrf shift (%+.2f, %+.2f) NMAD %.3f->%.3f sharp %.3f; "
        "nad83 shift (%+.2f, %+.2f) NMAD %.3f->%.3f sharp %.3f; informative %s",
        cell,
        *(out["itrf"][k] for k in ("dx_m", "dy_m", "nmad_before_m", "nmad_after_m", "sharpness")),
        *(out["nad83"][k] for k in ("dx_m", "dy_m", "nmad_before_m", "nmad_after_m", "sharpness")),
        out["informative"],
    )
    return out


def winner(s: dict, p: dict) -> str | None:
    """The reading this sample votes for, or None when it carries no datum information.

    A sample votes when one reading's residual shift is smaller by at least p['min_margin_m'] and also below
    p['max_winner_share'] of the distance between the two readings' shifts (the datum transform, about 1.1 m in
    Indiana). Where the flight line's own horizontal error is as large as that transform, both readings are far off and
    the smaller one says nothing about the datum (E_Hamilton E0565N4443: 0.82 vs 0.70 m; W_IN_W_2013-Posey E0409N4190:
    0.92 vs 1.92 m; 2026-11-07).
    """
    r = {h: float(np.hypot(s[h]["dx_m"], s[h]["dy_m"])) for h in HYPOTHESES}
    sep = float(np.hypot(s["itrf"]["dx_m"] - s["nad83"]["dx_m"], s["itrf"]["dy_m"] - s["nad83"]["dy_m"]))
    a, b = sorted(HYPOTHESES, key=r.get)
    return a if r[b] - r[a] >= p["min_margin_m"] and r[a] <= p["max_winner_share"] * sep else None


def decide(samples: list[dict], p: dict) -> str:
    """The reading all votes go to, with at least p['min_votes'] votes from informative samples, else ambiguous.

    The test is relative: the two readings differ by a fixed transform (about 1.1 m in Indiana), while one flight
    line's own horizontal error can leave a few decimetres under the right reading. An absolute residual threshold
    would mistake such line errors for a datum problem (W_IN_W_2013-Tippecanoe, 2026-11-05). Samples that do not vote
    (winner None) abstain; a vote for the other reading makes the delivery ambiguous.
    """
    votes = [w for s in samples if s["informative"] and (w := winner(s, p)) is not None]
    if len(votes) < p["min_votes"] or len(set(votes)) != 1:
        return "ambiguous"
    return votes[0]


def test_delivery(delivery: str, params) -> dict:
    """Run the test, write statewide/deliveries/<delivery>/datum.json, return it."""
    p = params.section("data_src.datum")
    cells, xy = candidate_cells(delivery)
    if not cells:
        raise ValueError(f"{delivery}: no cell is fully covered by its files")
    labels = _clusters(xy, p["n_samples"])
    rng = np.random.default_rng(0)
    out_dir = paths.delivery_dir(delivery)
    with Step("data_src.datum", out_dir=out_dir, params=p, unit=delivery) as step:
        samples = []
        for k in range(labels.max() + 1):
            members = [cells[i] for i in rng.permutation(np.flatnonzero(labels == k))]
            tried = 0
            for cell in members:
                if tried == MAX_TRIES:
                    break
                s = test_cell(cell, params)
                if s is None:  # no 3DEP ground here: does not count as a try
                    continue
                tried += 1
                samples.append(s)
                if s["informative"]:
                    break
        for smp in samples:
            smp["winner"] = winner(smp, p) if smp["informative"] else None
        doc = {
            "delivery_id": delivery,
            "decision": decide(samples, p),
            "geoid": f"{params['data_src.geoid.legacy_grid']} -> {params['data_src.geoid.project_grid']}",
            "max_residual_m": p["max_residual_m"],
            "samples": samples,
        }
        step.output(io.write_json(doc, out_dir / "datum.json", "datum"), "datum")
    log.info(
        "%s: datum %s from %d informative of %d samples",
        delivery,
        doc["decision"],
        sum(s["informative"] for s in samples),
        len(samples),
    )
    return doc


def redecide(delivery: str, params) -> dict:
    """Re-apply the current decision rule to an existing datum.json (no points are read)."""
    p = params.section("data_src.datum")
    out_dir = paths.delivery_dir(delivery)
    path = out_dir / "datum.json"
    doc = io.read_json(path, "datum")
    with Step("data_src.datum_redecide", out_dir=out_dir, params=p, unit=delivery) as step:
        step.input(path, "datum")
        for smp in doc["samples"]:
            smp["winner"] = winner(smp, p) if smp["informative"] else None
        doc["decision"] = decide(doc["samples"], p)
        step.output(io.write_json(doc, path, "datum"), "datum")
    return doc


def report(params, samples: bool = False):
    """One row per delivery with a datum.json (or, with samples, one row per sample), for human review."""
    import pandas as pd

    p = params.section("data_src.datum")
    rows = []
    for path in sorted((paths.data_root() / "statewide" / "deliveries").glob("*/datum.json")):
        doc = io.read_json(path, "datum")
        good = [s for s in doc["samples"] if s["informative"]]
        win = doc["decision"] if doc["decision"] != "ambiguous" else None
        resid = [float(np.hypot(s[win]["dx_m"], s[win]["dy_m"])) for s in good] if win else []
        if samples:
            for s in doc["samples"]:
                rows.append(
                    {
                        "delivery": doc["delivery_id"],
                        "cell": s["cell_id"],
                        "informative": s["informative"],
                        "winner": s.get("winner"),
                        "itrf_shift_m": round(float(np.hypot(s["itrf"]["dx_m"], s["itrf"]["dy_m"])), 2),
                        "nad83_shift_m": round(float(np.hypot(s["nad83"]["dx_m"], s["nad83"]["dy_m"])), 2),
                        "sharpness": min(s["itrf"]["sharpness"], s["nad83"]["sharpness"]),
                    }
                )
            continue
        rows.append(
            {
                "delivery": doc["delivery_id"],
                "decision": doc["decision"],
                "samples": len(doc["samples"]),
                "informative": len(good),
                "winner_max_shift_m": round(max(resid), 2) if resid else None,
                "warning": "line-level shift?"
                if resid and max(resid) > p["max_residual_m"]
                else "needs manual override"
                if doc["decision"] == "ambiguous"
                else "",
            }
        )
    return pd.DataFrame(rows)
