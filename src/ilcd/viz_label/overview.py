"""Site overview image: M3C2 distance over a hillshade of the core points' 3DEP ground (design.md section 7.2).

Pixels of viz_label.overview.res_m, north up, over the site's bounds snapped to the pixel size. Grey hillshade from
the mean core-point height; where the pixel's significant core points have a mean distance, that distance on a
red (loss) to blue (gain) ramp saturating at ramp_m; transparent where there is no core point.
"""

import numpy as np

LOSS = np.array([178, 24, 43], float)
GAIN = np.array([33, 102, 172], float)


def frame(bounds, res: float):
    """(xmin, ymin, width, height) of the overview grid covering bounds."""
    xmin, ymin = np.floor(bounds[0] / res) * res, np.floor(bounds[1] / res) * res
    xmax, ymax = np.ceil(bounds[2] / res) * res, np.ceil(bounds[3] / res) * res
    return float(xmin), float(ymin), int(round((xmax - xmin) / res)), int(round((ymax - ymin) / res))


class Accumulator:
    """Per-pixel sums over the cells' core points, so a site's ~10^8 points are never in memory at once."""

    def __init__(self, bounds, res: float):
        self.res = res
        self.xmin, self.ymin, self.w, self.h = frame(bounds, res)
        n = self.w * self.h
        self.nz, self.sz = np.zeros(n), np.zeros(n)
        self.nd, self.sd = np.zeros(n), np.zeros(n)

    def add(self, x, y, z, d, sig):
        i = np.floor((x - self.xmin) / self.res).astype(np.int64)
        j = np.floor((y - self.ymin) / self.res).astype(np.int64)
        ok = (i >= 0) & (i < self.w) & (j >= 0) & (j < self.h) & np.isfinite(z)
        k = j[ok] * self.w + i[ok]  # row 0 = south
        n = self.w * self.h
        self.nz += np.bincount(k, minlength=n)
        self.sz += np.bincount(k, weights=z[ok], minlength=n)
        s = sig[ok] & np.isfinite(d[ok])
        self.nd += np.bincount(k[s], minlength=n)
        self.sd += np.bincount(k[s], weights=d[ok][s], minlength=n)

    def rgba(self, ramp: float) -> np.ndarray:
        """h x w x 4 uint8, row 0 = north."""
        from ..common.raster import fill_nearest

        with np.errstate(invalid="ignore", divide="ignore"):
            z = (self.sz / self.nz).reshape(self.h, self.w)
            d = (self.sd / self.nd).reshape(self.h, self.w)
        has = np.isfinite(z)
        zf = fill_nearest(z) if has.any() else np.zeros_like(z)
        gy, gx = np.gradient(zf, self.res)
        az, alt = np.radians(315.0), np.radians(45.0)  # light from the north-west
        slope = np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)  # rows run south to north
        shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
        grey = 150 + 90 * np.clip(shade, 0, 1)
        rgb = np.repeat(grey[..., None], 3, axis=2)
        t = np.clip(np.abs(np.nan_to_num(d)) / ramp, 0, 1)[..., None]
        col = np.where((np.nan_to_num(d) < 0)[..., None], LOSS, GAIN)
        rgb = np.where(np.isfinite(d)[..., None], rgb * (1 - t) + col * t, rgb)
        a = np.where(has, 255, 0)
        return np.flipud(np.dstack([rgb, a]).round().astype(np.uint8))
