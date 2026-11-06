"""Correction status of each (cell, line) (contract `status`), from the held-out residuals of the cross-validation.

Rows: observations of the CV test kinds (eval.cv.test_kinds, hard ground), narrow or not; each row's residual is its
mean over the CV repeats of the final stage. Per (cell, line):
    bias = median, nmad = NMAD, lod95 = 1.96 * sqrt(bias^2 + nmad^2)    (the systematic part of a DoD pixel's LoD)
    status: no_data        fewer than eval.status.min_holdout rows
            not_corrected  |bias| or nmad above eval.status.corrected_max_nmad_m
            corrected      otherwise, when the model has a stage-2 field for the line
            stage1_only    otherwise
"""

import numpy as np
import pandas as pd

from ..common.raster import nmad


def table(d: pd.DataFrame, r: np.ndarray, test_kinds, field_lines, p: dict) -> pd.DataFrame:
    """d: CV rows (cell, line_id, kind); r: their held-out residuals; p = params.section('eval.status')."""
    m = d["kind"].isin(test_kinds).to_numpy() & np.isfinite(r)
    g = pd.DataFrame({"cell_id": d["cell"].to_numpy()[m], "line_id": d["line_id"].to_numpy()[m], "r": r[m]})
    out = g.groupby(["cell_id", "line_id"])["r"].agg(n_holdout="size", bias_m="median", nmad_m=nmad).reset_index()
    out["lod95_m"] = 1.96 * np.sqrt(out["bias_m"] ** 2 + out["nmad_m"] ** 2)
    lim = p["corrected_max_nmad_m"]
    bad = (out["bias_m"].abs() > lim) | (out["nmad_m"] > lim)
    out["status"] = np.where(
        out["n_holdout"] < p["min_holdout"],
        "no_data",
        np.where(bad, "not_corrected", np.where(out["line_id"].isin(field_lines), "corrected", "stage1_only")),
    )
    return out.sort_values(["cell_id", "line_id"]).reset_index(drop=True)
