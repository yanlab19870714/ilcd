"""The observation table a correction method is fitted on, assembled from a scope's contract files.

One row per non-screened (patch, pass) observation of a pass of the scope (cells are shared and also observe
passes of files outside a region; those rows are dropped), with its line and block, weight, along-track coordinate
and the fields evaluation needs (cell, narrow). The along-track coordinate follows the exploration
(fit_field.load): passes heading within 45 degrees of north or south use northing, the others easting, in km,
centred per (block, axis) by the weighted mean.
"""

import numpy as np
import pandas as pd

from ..common import ids, scope


def axis_of(heading_deg) -> np.ndarray:
    """'ns' or 'ew' per heading (old: quadrant NESW of the pass heading)."""
    q = ((np.asarray(heading_deg, float) + 45) % 360) // 90
    return np.where(np.isin(q, [0, 2]), "ns", "ew")


def assemble(sc: str, p: dict, step=None) -> pd.DataFrame:
    """p = params.section('align')."""
    obs = scope.gather_cells(sc, "obs", step).to_pandas()
    patches = scope.gather_cells(sc, "patches", step).to_pandas()[["patch_id", "narrow", "kind"]]
    screen = scope.read(sc, "refs", "screen").to_pandas()
    passes = scope.read(sc, "flightlines", "passes").to_pandas()[["pass_id", "heading_deg", "delivery_id"]]
    members = scope.read(sc, "flightlines", "line_members").to_pandas()[["pass_id", "line_id"]]
    lines = scope.read(sc, "flightlines", "lines").to_pandas()[["line_id", "block_id"]]
    n0 = len(obs)
    obs = obs[~obs["patch_id"].isin(screen["patch_id"])]
    d = (
        obs.merge(patches, on="patch_id", how="left", validate="many_to_one")
        .merge(passes, on="pass_id", how="left", validate="many_to_one")
        .merge(members, on="pass_id", how="left", validate="many_to_one")
        .merge(lines, on="line_id", how="left", validate="many_to_one")
    )
    # cells are shared by all regions and observe every file overlapping them; keep the scope's own passes only
    outside = d["line_id"].isna().to_numpy()
    d = d[~outside]
    d["cell"] = d["patch_id"].map(lambda s: ids.parse_patch(s)[0])
    d["w"] = 1.0 / (d["sigma_m"].to_numpy(float) ** 2 + p["stage1"]["sys_sd_m"] ** 2)
    d["axis"] = axis_of(d["heading_deg"])
    d["along_raw_km"] = np.where(d["axis"] == "ns", d["y_m"], d["x_m"]) / 1000.0
    origin = {}
    for (b, ax), g in d.groupby(["block_id", "axis"]):
        origin[(b, ax)] = float(np.average(g["along_raw_km"], weights=g["w"]))
    d["along_km"] = d["along_raw_km"] - [origin[(b, a)] for b, a in zip(d["block_id"], d["axis"], strict=True)]
    d.attrs["along_origin_km"] = origin
    d.attrs["n_screened"] = n0 - len(obs)
    d.attrs["n_outside_scope"] = int(outside.sum())
    return d.reset_index(drop=True)
