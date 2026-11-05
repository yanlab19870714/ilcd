"""Scope-level screening of reference patches (contract `screen`); needs neighbours across cells.

change_suspect (old measure_patches.flag_change): a patch whose pooled 2011 offset departs from the weighted median
of patches within refs.screen.near_m by more than max(flag_min_m, flag_k sqrt(se^2 + scatter^2)), where scatter is
the NMAD of all departures (floored at sys_sd_m), so noisier surfaces get a wider tolerance.

gross_cell (old fit_field.load): every patch of a cell whose median observation (of unflagged patches) is more than
refs.screen.cell_gross_m from the scope median; alignment errors are centimetres, so this is real change.
"""

import numpy as np
import pandas as pd
import pyarrow as pa
from scipy.spatial import cKDTree

from ..common import contracts, ids
from ..common.raster import nmad


def _weighted_median(v: np.ndarray, w: np.ndarray) -> float:
    o = np.argsort(v)
    cw = np.cumsum(w[o])
    return float(v[o][np.searchsorted(cw, cw[-1] / 2)])


def change_suspects(patches: pd.DataFrame, p: dict) -> pd.DataFrame:
    """patch_id, deviation_m of flagged patches. `patches` has patch_id, x, y, legacy_dz_m, legacy_se_m, n_legacy."""
    good = patches[(patches["n_legacy"] >= p["min_points"]) & patches["legacy_dz_m"].notna()].reset_index(drop=True)
    if len(good) < p["min_neighbours"] + 1:
        return pd.DataFrame({"patch_id": [], "deviation_m": []})
    xy = good[["x", "y"]].to_numpy()
    off, se = good["legacy_dz_m"].to_numpy(float), good["legacy_se_m"].to_numpy(float)
    w = 1 / (se**2 + p["sys_sd_m"] ** 2)
    tree = cKDTree(xy)
    nbr = np.full(len(good), np.nan)
    for k, nb in enumerate(tree.query_ball_point(xy, p["near_m"])):
        nb = [i for i in nb if i != k and np.hypot(*(xy[i] - xy[k])) < p["near_m"]]
        if len(nb) >= p["min_neighbours"]:
            nbr[k] = _weighted_median(off[nb], w[nb])
    dev = off - nbr
    has = np.isfinite(dev)
    scatter = max(p["sys_sd_m"], nmad(dev[has])) if has.any() else p["sys_sd_m"]
    flag = np.zeros(len(good), bool)
    flag[has] = np.abs(dev[has]) > np.maximum(p["flag_min_m"], p["flag_k"] * np.sqrt(se[has] ** 2 + scatter**2))
    return pd.DataFrame({"patch_id": good["patch_id"][flag].to_numpy(), "deviation_m": dev[flag]})


def gross_cells(obs: pd.DataFrame, flagged: set, p: dict) -> pd.DataFrame:
    """patch_id, deviation_m of every patch in a cell whose median obs departs from the scope median."""
    o = obs[~obs["patch_id"].isin(flagged)].copy()
    if o.empty:
        return pd.DataFrame({"patch_id": [], "deviation_m": []})
    o["cell"] = o["patch_id"].map(lambda s: ids.parse_patch(s)[0])
    ref = float(np.median(o["dz_m"]))
    med = o.groupby("cell")["dz_m"].median() - ref
    bad = med[np.abs(med) > p["cell_gross_m"]]
    hit = obs.assign(cell=obs["patch_id"].map(lambda s: ids.parse_patch(s)[0]))
    hit = hit[hit["cell"].isin(bad.index)].drop_duplicates("patch_id")
    return pd.DataFrame({"patch_id": hit["patch_id"].to_numpy(), "deviation_m": bad.reindex(hit["cell"]).to_numpy()})


def screen(patches: pd.DataFrame, obs: pd.DataFrame, p: dict) -> pa.Table:
    cs = change_suspects(patches, p).assign(reason="change_suspect")
    gc = gross_cells(obs, set(cs["patch_id"]), p).assign(reason="gross_cell")
    gc = gc[~gc["patch_id"].isin(cs["patch_id"])]
    out = pd.concat([cs, gc], ignore_index=True)[["patch_id", "reason", "deviation_m"]]
    return pa.Table.from_pandas(out, schema=contracts.get("screen").arrow_schema(), preserve_index=False)
