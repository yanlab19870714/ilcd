"""Stage 1 of the correction: one weighted least-squares fit (old fit_scan.py).

    d = dz0[line] + trend[block] * along_km + spline_block(scan) + offset[3DEP batch] + e,
    weight 1 / (sigma^2 + sys_sd^2)

The spline is a cubic B-spline with align.stage1.scan_knots uniform knots over the block's scan-angle range
(scan_range_pct percentiles); a small ridge on its coefficients removes the constant it shares with the line
offsets. terms selects what is fitted: ("line_offset", "block_trend") is the exploration's "M3",
adding "block_scan_spline" gives "S_8". "modern_offset" adds one vertical offset per 3DEP batch, the batch with
the most observations fixed at 0 (align.modern_reference_batch); with a single batch it adds nothing.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.interpolate import BSpline

TERMS_M3 = ("line_offset", "block_trend", "modern_offset")
TERMS_S1 = ("line_offset", "block_trend", "modern_offset", "block_scan_spline")


def spline_basis(theta, k: int, lo: float, hi: float) -> np.ndarray:
    t = np.r_[[lo] * 3, np.linspace(lo, hi, k), [hi] * 3]
    return BSpline.design_matrix(np.clip(np.asarray(theta, float), lo, hi), t, 3).toarray()


@dataclass
class Stage1:
    terms: tuple
    lines: dict  # line_id -> dz0
    trend: dict  # block_id -> m per km
    spline: dict  # block_id -> (lo, hi, k, coef)
    median_offset: float
    modern: dict = field(default_factory=dict)  # 3DEP batch -> offset (reference batch 0)
    meta: dict = field(default_factory=dict)

    def predict(self, d: pd.DataFrame) -> np.ndarray:
        out = d["line_id"].map(self.lines).fillna(self.median_offset).to_numpy(float, copy=True)
        if "block_trend" in self.terms:
            out += d["block_id"].map(self.trend).fillna(0.0).to_numpy(float) * d["along_km"].to_numpy(float)
        if self.modern and "modern_batch" in d:
            out += d["modern_batch"].map(self.modern).fillna(0.0).to_numpy(float)
        if "block_scan_spline" in self.terms:
            for b, (lo, hi, k, coef) in self.spline.items():
                m = (d["block_id"] == b).to_numpy()
                if m.any():
                    out[m] += spline_basis(d["scan_deg"].to_numpy()[m], k, lo, hi) @ coef
        return out


def scan_ranges(d: pd.DataFrame, p: dict) -> dict:
    lo_pct, hi_pct = p["scan_range_pct"]
    return {
        b: (float(np.percentile(g["scan_deg"], lo_pct)), float(np.percentile(g["scan_deg"], hi_pct)))
        for b, g in d.groupby("block_id")
    }


def fit(d: pd.DataFrame, p: dict, terms=TERMS_S1, ranges: dict | None = None) -> Stage1:
    """p = params.section('align.stage1'); ranges: per-block scan range (defaults to this data's)."""
    ranges = ranges or scan_ranges(d, p)
    lines = sorted(d["line_id"].unique())
    blocks = sorted(d["block_id"].unique())
    li = d["line_id"].map({v: i for i, v in enumerate(lines)}).to_numpy()
    n = len(d)
    cols = [np.eye(len(lines))[li]]
    if "block_trend" in terms:
        T = np.zeros((n, len(blocks)))
        for j, b in enumerate(blocks):
            m = (d["block_id"] == b).to_numpy()
            T[m, j] = d["along_km"].to_numpy(float)[m]
        cols.append(T)
    batches = []
    if "modern_offset" in terms and "modern_batch" in d and d["modern_batch"].nunique() > 1:
        counts = d["modern_batch"].value_counts()
        ref = counts.index[0] if p["modern_reference_batch"] == "auto" else p["modern_reference_batch"]
        if ref not in counts.index:
            raise ValueError(f"reference 3DEP batch {ref} has no observations")
        batches = sorted(b for b in counts.index if b != ref)
        M = np.zeros((n, len(batches)))
        for j, b in enumerate(batches):
            M[(d["modern_batch"] == b).to_numpy(), j] = 1.0
        cols.append(M)
    k = p["scan_knots"]
    nb = k + 2
    if "block_scan_spline" in terms:
        S = np.zeros((n, nb * len(blocks)))
        for j, b in enumerate(blocks):
            m = (d["block_id"] == b).to_numpy()
            S[m, j * nb : (j + 1) * nb] = spline_basis(d["scan_deg"].to_numpy()[m], k, *ranges[b])
        cols.append(S)
    A = np.hstack(cols)
    w = d["w"].to_numpy(float)
    sw = np.sqrt(w)
    n_sp = nb * len(blocks) if "block_scan_spline" in terms else 0
    R = np.zeros((n_sp, A.shape[1]))
    if n_sp:
        R[:, A.shape[1] - n_sp :] = np.eye(n_sp) * np.sqrt(p["ridge"] * np.median(w))
    coef, *_ = np.linalg.lstsq(
        np.vstack([A * sw[:, None], R]), np.r_[d["dz_m"].to_numpy(float) * sw, np.zeros(n_sp)], rcond=None
    )
    a = coef[: len(lines)]
    off = len(lines)
    trend = {}
    if "block_trend" in terms:
        trend = {b: float(coef[off + j]) for j, b in enumerate(blocks)}
        off += len(blocks)
    modern = {}
    if batches:
        modern = {ref: 0.0} | {b: float(coef[off + j]) for j, b in enumerate(batches)}
        off += len(batches)
    spline = {}
    if n_sp:
        spline = {b: (*ranges[b], k, coef[off + j * nb : off + (j + 1) * nb]) for j, b in enumerate(blocks)}
    return Stage1(
        terms=tuple(terms),
        lines={v: float(x) for v, x in zip(lines, a, strict=True)},
        trend=trend,
        spline=spline,
        median_offset=float(np.median(a)),
        modern=modern,
        meta={"n_obs": n, "modern_ref": ref if batches else None},
    )
