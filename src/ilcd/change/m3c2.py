"""M3C2 (Lague et al. 2013, py4dgeo) of one cell of a site (contract `m3c2`; docs/design.md section 7.1).

As in the exploration (old m3c2_sites.py): core points are 3DEP ground thinned to one per change.m3c2.core_res_m
cell, normals from 3DEP ground at the most planar of normal_radii_m, oriented up, so a negative distance is surface
loss. Both epochs are read with the cell padded by max_distance_m; core points are those inside the cell. The
registration error is the cell's systematic SD from eval/status (dod.sys_sd), weighted by held-out rows.
"""

import numpy as np
import pandas as pd

from ..common import grid
from .dod import GROUND, sys_sd


def thin(xyz: np.ndarray, res: float) -> np.ndarray:
    """One point per res x res cell (the first in file order), as old m3c2_sites.thin."""
    _, i = np.unique(np.floor(xyz[:, :2] / res).astype(np.int64), axis=0, return_index=True)
    return xyz[np.sort(i)]


def registration_error(status: pd.DataFrame, cell: str) -> float:
    by_line, fallback = sys_sd(status, cell)
    rows = status[(status["cell_id"] == cell) & status["line_id"].isin(by_line)]
    if rows.empty:
        return fallback
    w = rows["n_holdout"].to_numpy(float)
    s = rows["line_id"].map(by_line).to_numpy(float)
    return float(np.sqrt(np.sum(w * s**2) / np.sum(w)))


def core_points(cell: str, mod: dict, res: float) -> np.ndarray:
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    g = mod["cls"] == GROUND
    inside = g & (mod["x"] >= xmin) & (mod["x"] < xmax) & (mod["y"] >= ymin) & (mod["y"] < ymax)
    return thin(np.c_[mod["x"][inside], mod["y"][inside], mod["z"][inside]], res)


def run(leg_xyz: np.ndarray, mod_xyz: np.ndarray, core: np.ndarray, reg_err: float, p: dict) -> pd.DataFrame:
    """Rows of the contract `m3c2`. p = params.section('change.m3c2')."""
    import py4dgeo

    e_leg, e_mod = py4dgeo.Epoch(leg_xyz.astype(np.float64)), py4dgeo.Epoch(mod_xyz.astype(np.float64))
    m = py4dgeo.M3C2(
        epochs=(e_leg, e_mod),
        corepoints=core.astype(np.float64),
        cyl_radius=p["cylinder_radius_m"],
        normal_radii=tuple(p["normal_radii_m"]),
        max_distance=p["max_distance_m"],
        registration_error=reg_err,
        cloud_for_normals=e_mod,
    )
    dist, unc = m.run()
    nrm = m.directions()
    scale = m.directions_radii() if hasattr(m, "directions_radii") else np.full(len(core), np.nan)
    ok = np.isfinite(dist)
    lod = np.asarray(unc["lodetection"], float)
    return pd.DataFrame(
        {
            "core_id": np.arange(len(core), dtype=np.int64),
            "x_m": core[:, 0],
            "y_m": core[:, 1],
            "z_m": core[:, 2],
            "d_m": np.where(ok, dist, np.nan).astype(np.float32),
            "lod_m": np.where(np.isfinite(lod), lod, np.nan).astype(np.float32),
            "sig": ok & np.isfinite(lod) & (np.abs(dist) > lod),
            "normal_scale_m": np.asarray(scale, np.float32),
            "normal_z": np.abs(nrm[:, 2]).astype(np.float32),
            "n_legacy": np.asarray(unc["num_samples1"], np.int32),
            "n_modern": np.asarray(unc["num_samples2"], np.int32),
        }
    )
