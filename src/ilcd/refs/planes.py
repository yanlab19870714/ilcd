"""Local planes on a 1 m grid from a sliding 3 x 3 m window (old patches_v2.window_planes).

Each grid cell gets the least-squares plane of all points in its 3 x 3 neighbourhood, from box-filtered moments,
so planes follow road edges and roof outlines. The grid is an ilcd.common.raster.Grid (row 0 = south).
"""

import numpy as np
from scipy import ndimage

from ..common.raster import Grid

WIN = 3


def window_planes(G: Grid, x, y, z) -> dict:
    """Arrays (ny, nx): n (points in window), a, b (dz/dx, dz/dy), zc (plane height at the cell centre), rms, slope."""
    i, j, m = G.index(x, y)
    i, j = np.clip(i, 0, G.nx - 1), np.clip(j, 0, G.ny - 1)
    x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
    xl, yl = x - G.x0, y - G.y0  # local coordinates keep the moments well conditioned
    z0 = float(np.median(z)) if z.size else 0.0
    zz = z - z0
    k = j * G.nx + i
    mom = {}
    for key, w in (
        ("1", np.ones_like(xl)),
        ("x", xl),
        ("y", yl),
        ("z", zz),
        ("xx", xl * xl),
        ("xy", xl * yl),
        ("yy", yl * yl),
        ("xz", xl * zz),
        ("yz", yl * zz),
        ("zz", zz * zz),
    ):
        g = np.bincount(k, w, G.nx * G.ny).reshape(G.ny, G.nx)
        mom[key] = ndimage.uniform_filter(g, size=WIN, mode="constant") * WIN * WIN
    n = mom["1"]
    with np.errstate(invalid="ignore", divide="ignore"):
        mx, my, mz = mom["x"] / n, mom["y"] / n, mom["z"] / n
        cxx, cxy, cyy = mom["xx"] - mom["x"] * mx, mom["xy"] - mom["x"] * my, mom["yy"] - mom["y"] * my
        cxz, cyz, czz = mom["xz"] - mom["x"] * mz, mom["yz"] - mom["y"] * mz, mom["zz"] - mom["z"] * mz
        det = cxx * cyy - cxy * cxy
        a = (cxz * cyy - cyz * cxy) / det
        b = (cyz * cxx - cxz * cxy) / det
        rms = np.sqrt(np.maximum(czz - a * cxz - b * cyz, 0) / n)
    bad = (n < 3) | (np.abs(det) < 1e-9)
    a[bad] = b[bad] = rms[bad] = np.nan
    xc, yc = G.centers()
    XC, YC = np.meshgrid(xc - G.x0, yc - G.y0)
    zc = mz + a * (XC - mx) + b * (YC - my) + z0
    return {"n": n, "a": a, "b": b, "zc": zc, "rms": rms, "slope": np.degrees(np.arctan(np.hypot(a, b)))}


def plane_residual(G: Grid, P: dict, x, y, z) -> np.ndarray:
    """z minus the plane of the point's cell (NaN outside the grid or where the cell has no plane)."""
    i, j, m = G.index(x, y)
    out = np.full(np.size(x), np.nan)
    ii, jj = i[m], j[m]
    xc = G.x0 + (ii + 0.5) * G.res
    yc = G.y0 + (jj + 0.5) * G.res
    out[m] = np.asarray(z)[m] - (
        P["zc"][jj, ii] + P["a"][jj, ii] * (np.asarray(x)[m] - xc) + P["b"][jj, ii] * (np.asarray(y)[m] - yc)
    )
    return out


def per_cell_mean(G: Grid, x, y, v) -> np.ndarray:
    return G.mean(x, y, v)
