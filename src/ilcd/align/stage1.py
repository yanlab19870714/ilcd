"""Stage 1 of the correction: one weighted least-squares fit (old fit_scan.py).

    d = dz0[line] + trend[block] * along_km + spline_block(scan) + offset[3DEP batch] + e,
    weight 1 / (sigma^2 + sys_sd^2)

The spline is a cubic B-spline with align.stage1.scan_knots uniform knots over the block's scan-angle range
(scan_range_pct percentiles); a small ridge on its coefficients removes the constant it shares with the line
offsets. terms selects what is fitted: ("line_offset", "block_trend") is the exploration's "M3",
adding "block_scan_spline" gives "S_8". "modern_offset" adds one vertical offset per 3DEP batch, the batch with
the most observations fixed at 0 (align.modern_reference_batch); with a single batch it adds nothing.

"line_hshift" (old hshift_fit.py, sensor frame): a 2011 point sits at its true position + s, with
    s = forward * (sin h, cos h) + left * (-cos h, sin h),
    left = left_L + b_left * scan,   forward = fwd_L + b_fwd * scan
(h the pass heading), so on ground with 3DEP gradient g the observation 2011 - 3DEP gains -(g . s). left_L, fwd_L are
per line for lines with at least align.stage1.hshift_min_obs sloped-ground observations; the others share their
block's (pooled, as the exploration's v3d); b_left, b_fwd are per block.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.interpolate import BSpline

TERMS_M3 = ("line_offset", "block_trend", "modern_offset")
TERMS_S1 = ("line_offset", "block_trend", "modern_offset", "block_scan_spline")
TERMS_S1H = TERMS_S1 + ("line_hshift",)


def hshift_columns(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(-dz/d left, -dz/d forward) per row: the observation change per metre of left / forward shift."""
    h = np.radians(d["heading_deg"].to_numpy(float))
    gx, gy = d["gx"].to_numpy(float), d["gy"].to_numpy(float)
    gl = gx * -np.cos(h) + gy * np.sin(h)
    gf = gx * np.sin(h) + gy * np.cos(h)
    return -gl, -gf


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
    hshift: dict = field(default_factory=dict)  # line_id -> (left, forward, pooled)
    hscan: dict = field(default_factory=dict)  # block_id -> (b_left, b_fwd) per degree of scan angle
    meta: dict = field(default_factory=dict)

    def predict(self, d: pd.DataFrame) -> np.ndarray:
        out = d["line_id"].map(self.lines).fillna(self.median_offset).to_numpy(float, copy=True)
        if "block_trend" in self.terms:
            out += d["block_id"].map(self.trend).fillna(0.0).to_numpy(float) * d["along_km"].to_numpy(float)
        if self.hshift:
            cl, cf = hshift_columns(d)
            lf = d["line_id"].map(lambda x: self.hshift.get(x, (0.0, 0.0, True)))
            left = np.array([v[0] for v in lf])
            fwd = np.array([v[1] for v in lf])
            bs = d["block_id"].map(lambda b: self.hscan.get(b, (0.0, 0.0)))
            scan = d["scan_deg"].to_numpy(float)
            left = left + np.array([v[0] for v in bs]) * scan
            fwd = fwd + np.array([v[1] for v in bs]) * scan
            out += cl * left + cf * fwd
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
    own, pooled_blocks = [], []
    if "line_hshift" in terms:
        cl, cf = hshift_columns(d)
        slope_n = d.loc[d["kind"] == "natural_slope", "line_id"].value_counts()
        own = sorted(slope_n.index[slope_n >= p["hshift_min_obs"]])
        H = np.zeros((n, 2 * len(own)))
        for j, lid in enumerate(own):
            m = (d["line_id"] == lid).to_numpy()
            H[m, 2 * j], H[m, 2 * j + 1] = cl[m], cf[m]
        pooled = ~d["line_id"].isin(own).to_numpy()
        pooled_blocks = blocks
        Hb = np.zeros((n, 4 * len(blocks)))  # per block: pooled left, pooled forward, scan slope left, forward
        scan = d["scan_deg"].to_numpy(float)
        for j, b in enumerate(blocks):
            m = (d["block_id"] == b).to_numpy()
            Hb[m & pooled, 4 * j], Hb[m & pooled, 4 * j + 1] = cl[m & pooled], cf[m & pooled]
            Hb[m, 4 * j + 2], Hb[m, 4 * j + 3] = cl[m] * scan[m], cf[m] * scan[m]
        cols += [H, Hb]
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
    hshift, hscan = {}, {}
    if "line_hshift" in terms:
        for j, lid in enumerate(own):
            hshift[lid] = (float(coef[off + 2 * j]), float(coef[off + 2 * j + 1]), False)
        off += 2 * len(own)
        for j, b in enumerate(pooled_blocks):
            pl, pf, sl, sf = (float(v) for v in coef[off + 4 * j : off + 4 * j + 4])
            hscan[b] = (sl, sf)
            for lid in d.loc[(d["block_id"] == b) & ~d["line_id"].isin(own), "line_id"].unique():
                hshift[lid] = (pl, pf, True)
        off += 4 * len(pooled_blocks)
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
        hshift=hshift,
        hscan=hscan,
        meta={"n_obs": n, "modern_ref": ref if batches else None},
    )
