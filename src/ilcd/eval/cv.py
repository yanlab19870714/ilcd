"""Cross-validation of a correction method (contract `cv`).

Folds are spatial units (eval.cv.fold_unit: cell (default), patch, or file): repeats x folds random splits with a
fixed seed; each fold refits the method on the other folds and predicts the held-out rows. Cell folds keep every
patch on one side (a patch lies in one cell); file folds do not, since a patch crossing a file boundary is observed
by passes of both files. The score is the exploration's "segment RMS" (fit_tradeoff.cv): residuals of held-out
narrow (road-like) patches, median per (eval.cv.segment_unit, pass), RMS over segments.
Scan-angle knot ranges come from all rows, so folds share the spline support.
"""

import numpy as np
import pandas as pd

from ..common.raster import nmad


def fold_units(d: pd.DataFrame, kind: str) -> np.ndarray:
    """Spatial unit of every row: its cell, its patch, the legacy file of its pass, or its patch's home file."""
    if kind == "cell":
        return d["cell"].to_numpy()
    if kind == "patch":
        return d["patch_id"].to_numpy()
    if kind == "file":
        return d["pass_id"].str.rsplit("#", n=1).str[0].to_numpy()
    if kind == "patch_file":
        # home file of each patch: the file whose passes contribute most 2011 points to it. Folding by it keeps
        # every patch on one side, and (home file x pass) segments reproduce the exploration's tile x pass
        f = d["pass_id"].str.rsplit("#", n=1).str[0]
        n = d.assign(_f=f).groupby(["patch_id", "_f"])["n_legacy"].sum().reset_index()
        home = n.sort_values(["patch_id", "n_legacy", "_f"], ascending=[True, False, True]).drop_duplicates("patch_id")
        return d["patch_id"].map(home.set_index("patch_id")["_f"]).to_numpy()
    raise ValueError(f"unknown fold unit {kind!r}")


def folds(units: np.ndarray, n_folds: int, rng) -> dict:
    u = np.array(sorted(set(units.tolist())))
    return dict(zip(u, rng.permutation(u.size) % n_folds, strict=True))


def run(d: pd.DataFrame, stages: dict, p: dict) -> list[dict]:
    """stages: name -> fit(train) returning an object with predict(test). p = params.section('eval.cv')."""
    unit = fold_units(d, p["fold_unit"])
    seg_unit = fold_units(d, p["segment_unit"])
    rng = np.random.default_rng(0)
    acc = {s: {"seg": [], "nar": []} for s in stages}
    for _ in range(p["repeats"]):
        f = pd.Series(unit).map(folds(unit, p["folds"], rng)).to_numpy()
        for k in range(p["folds"]):
            tr, te = d[f != k], d[f == k]
            if tr.empty or te.empty:
                continue
            nar = te["narrow"].to_numpy(bool)
            seg_key = (pd.Series(seg_unit[f == k][nar]) + "|" + te.loc[nar, "pass_id"].to_numpy()).to_numpy()
            for s, fit in stages.items():
                r = te["dz_m"].to_numpy() - fit(tr).predict(te)
                acc[s]["nar"].append(r[nar])
                acc[s]["seg"].append(pd.Series(r[nar]).groupby(seg_key).median().to_numpy())
    out = []
    for s, a in acc.items():
        seg, nar = np.concatenate(a["seg"]), np.concatenate(a["nar"])
        out.append({"name": s, "rms_m": float(np.sqrt(np.mean(seg**2))), "nmad_m": nmad(nar), "n": int(seg.size)})
    return out
