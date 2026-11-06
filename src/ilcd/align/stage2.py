"""Stage 2: a smooth vertical correction field per flight line (old fit_field.Field, Glira-style, simplified).

For each line, f(x, y) = sum c_jk B_j(x) B_k(y): cubic B-splines with knots every align.stage2.spacing_m over the
line's observations (padded by half a spacing), fitted to the stage-1 residuals by weighted least squares with a
second-difference smoothness penalty (weight lam, in units of the median observation weight) and a small ridge.
One robust pass drops residuals beyond max(3 NMAD, 3 cm) and refits. Outside a line's knot box the field is 0.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.interpolate import BSpline
from scipy.sparse.linalg import lsqr


def _knots(lo: float, hi: float, h: float) -> np.ndarray:
    n = max(int(np.ceil((hi - lo) / h)), 1)
    inner = lo + np.arange(n + 1) * h
    return np.r_[[inner[0]] * 3, inner, [inner[-1]] * 3]


def _d2(n: int):
    if n < 3:
        return sparse.csr_matrix((0, n))
    return sparse.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(n - 2, n))


def basis(tx: np.ndarray, ty: np.ndarray, x, y):
    """Tensor B-spline design matrix (rows: points; coefficient index jx * ny + jy); 0 outside the knot box."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    inside = (x >= tx[0]) & (x <= tx[-1]) & (y >= ty[0]) & (y <= ty[-1])
    bx = BSpline.design_matrix(np.clip(x, tx[0], tx[-1]), tx, 3).toarray()
    by = BSpline.design_matrix(np.clip(y, ty[0], ty[-1]), ty, 3).toarray()
    b = (bx[:, :, None] * by[:, None, :]).reshape(x.size, -1)
    return sparse.csr_matrix(b * inside[:, None])


@dataclass
class Stage2:
    spacing_m: float
    lam: float
    lines: dict = field(default_factory=dict)  # line_id -> (tx, ty, coef)

    def predict(self, d: pd.DataFrame) -> np.ndarray:
        out = np.zeros(len(d))
        lines = d["line_id"].to_numpy()
        for lid, (tx, ty, c) in self.lines.items():
            m = lines == lid
            if m.any():
                out[m] = basis(tx, ty, d["x_m"].to_numpy()[m], d["y_m"].to_numpy()[m]) @ c
        return out


def fit(d: pd.DataFrame, r: np.ndarray, p: dict) -> Stage2:
    """d: rows with line_id, x_m, y_m, w; r: their stage-1 residuals. p = params.section('align.stage2')."""
    h, lam, ridge = p["spacing_m"], p["lam"], p["ridge"]
    out = Stage2(spacing_m=h, lam=lam)
    if len(d) == 0:
        return out
    wmed = float(np.median(d["w"]))
    lines = d["line_id"].to_numpy()
    for lid in np.unique(lines):
        m = lines == lid
        x, y = d["x_m"].to_numpy()[m], d["y_m"].to_numpy()[m]
        rr, w = np.asarray(r)[m], d["w"].to_numpy()[m]
        tx, ty = _knots(x.min() - h / 2, x.max() + h / 2, h), _knots(y.min() - h / 2, y.max() + h / 2, h)
        nx, ny = tx.size - 4, ty.size - 4
        B = basis(tx, ty, x, y)
        pen = [sparse.kron(_d2(nx), sparse.eye(ny)), sparse.kron(sparse.eye(nx), _d2(ny))]
        keep = np.ones(rr.size, bool)
        for _ in range(2):
            sw = np.sqrt(w * keep)
            A = sparse.vstack(
                [sparse.diags(sw) @ B]
                + [q * np.sqrt(lam * wmed) for q in pen if q.shape[0]]
                + [sparse.eye(nx * ny) * np.sqrt(ridge * wmed)]
            ).tocsr()
            b = np.r_[rr * sw, np.zeros(A.shape[0] - rr.size)]
            c = lsqr(A, b, atol=1e-10, btol=1e-10, iter_lim=5000)[0]
            res = rr - B @ c
            s = 1.4826 * np.median(np.abs(res[keep] - np.median(res[keep])))
            keep = np.abs(res) < max(3 * s, 0.03)
        out.lines[lid] = (tx, ty, c)
    return out
