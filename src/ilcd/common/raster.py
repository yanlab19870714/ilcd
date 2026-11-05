"""In-memory analysis grids: binning points into cells, filling holes, smoothing, interpolating.

A Grid has its origin at the lower-left corner; array index [j, i] is row j (from the south) and column i (from the
west). GeoTIFF writing flips rows to north-up (see ilcd.common.grid.pixel_grid for the on-disk alignment).
"""

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.interpolate import RegularGridInterpolator


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    res: float
    nx: int
    ny: int

    @classmethod
    def covering(cls, xmin: float, ymin: float, xmax: float, ymax: float, res: float) -> "Grid":
        return cls(xmin, ymin, res, int(np.ceil((xmax - xmin) / res)), int(np.ceil((ymax - ymin) / res)))

    def index(self, x, y):
        """(i, j, inside) of points."""
        i = np.floor((np.asarray(x) - self.x0) / self.res).astype(np.int64)
        j = np.floor((np.asarray(y) - self.y0) / self.res).astype(np.int64)
        return i, j, (i >= 0) & (i < self.nx) & (j >= 0) & (j < self.ny)

    def centers(self):
        return self.x0 + (np.arange(self.nx) + 0.5) * self.res, self.y0 + (np.arange(self.ny) + 0.5) * self.res

    def count(self, x, y) -> np.ndarray:
        i, j, m = self.index(x, y)
        return np.bincount(j[m] * self.nx + i[m], minlength=self.nx * self.ny).reshape(self.ny, self.nx)

    def mean(self, x, y, v) -> np.ndarray:
        """Mean of v per cell; NaN where empty."""
        i, j, m = self.index(x, y)
        k = j[m] * self.nx + i[m]
        n = np.bincount(k, minlength=self.nx * self.ny)
        s = np.bincount(k, weights=np.asarray(v, float)[m], minlength=self.nx * self.ny)
        with np.errstate(invalid="ignore", divide="ignore"):
            out = s / n
        return out.reshape(self.ny, self.nx)

    def interpolator(self, a: np.ndarray, fill_value=np.nan):
        """f(x, y) bilinear on cell centres."""
        xc, yc = self.centers()
        f = RegularGridInterpolator((yc, xc), a, bounds_error=False, fill_value=fill_value)
        return lambda x, y: f(np.c_[np.asarray(y, float), np.asarray(x, float)])


def fill_nearest(a: np.ndarray) -> np.ndarray:
    """Replace NaNs by the nearest finite value (old pair_diagnostics.fill_nearest)."""
    bad = ~np.isfinite(a)
    if not bad.any():
        return a.copy()
    if bad.all():
        raise ValueError("grid has no finite value")
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    return a[tuple(idx)]


def smooth(a: np.ndarray, sigma_cells: float) -> np.ndarray:
    return ndimage.gaussian_filter(a, sigma_cells)


def nmad(v) -> float:
    """Normalised median absolute deviation, ignoring NaN."""
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size else float("nan")
