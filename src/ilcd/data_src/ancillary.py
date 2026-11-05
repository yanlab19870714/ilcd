"""Ancillary layers per cell, cached under cache/ancillary/ (raw inputs, not contracts).

ndvi(cell, year)  NAIP NDVI on the cell's 1 m grid (Microsoft Planetary Computer; old naip_ndvi.py), every scene
                  touching the cell mosaicked in STAC order; NaN where no NAIP covers it. Row 0 = south.
"""

import random
import time

import numpy as np

from ..common import crs, grid, paths
from ..common.io import atomic_path
from ..common.log import get_logger
from . import http

log = get_logger(__name__)
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/naip"
RES = 1.0


def _json(method: str, url: str, **kw):
    """Planetary Computer answers 429 under load: back off with jitter, beyond the session's own retries."""
    for i in range(7):
        r = http.session().request(method, url, timeout=120, **kw)
        if r.status_code not in (429, 500, 502, 503, 504):
            r.raise_for_status()
            return r.json()
        time.sleep(15 * 2**i + random.uniform(0, 10))
    r.raise_for_status()


def naip_items(cell: str, year: int) -> list[dict]:
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    lon, lat = crs.to_lonlat([xmin, xmax, xmin, xmax], [ymin, ymin, ymax, ymax])
    body = {
        "collections": ["naip"],
        "bbox": [min(lon), min(lat), max(lon), max(lat)],
        "datetime": f"{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z",
        "limit": 50,
    }
    return _json("POST", STAC, json=body)["features"]


def ndvi_path(cell: str, year: int):
    return paths.cache_ancillary(f"naip_ndvi/{year}") / f"{cell}.tif"


def fetch_ndvi(cell: str, year: int):
    """Build and cache the NDVI GeoTIFF of a cell (north-up, EPSG:6345, 1 m)."""
    import rasterio
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject

    out = ndvi_path(cell, year)
    if out.exists():
        return out
    items = naip_items(cell, year)
    if not items:
        raise RuntimeError(f"no NAIP {year} over cell {cell}")
    token = _json("GET", TOKEN)["token"]
    t, w, h = grid.pixel_grid(cell, RES)
    dst = Affine(*t)
    red = np.full((h, w), np.nan, np.float32)
    nir = np.full((h, w), np.nan, np.float32)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        for it in items:
            with rasterio.open(f"{it['assets']['image']['href']}?{token}") as src:
                for band, acc in ((1, red), (4, nir)):
                    tmp = np.full((h, w), np.nan, np.float32)
                    reproject(
                        rasterio.band(src, band),
                        tmp,
                        src_nodata=0,
                        dst_transform=dst,
                        dst_crs=crs.PROJECT_CRS,
                        dst_nodata=np.nan,
                        resampling=Resampling.bilinear,
                    )
                    fill = np.isnan(acc) & np.isfinite(tmp)
                    acc[fill] = tmp[fill]
    with np.errstate(invalid="ignore", divide="ignore"):
        nd = ((nir - red) / (nir + red)).astype(np.float32)
    with atomic_path(out) as tmp:
        with rasterio.open(
            tmp,
            "w",
            driver="GTiff",
            width=w,
            height=h,
            count=1,
            dtype="float32",
            crs=crs.PROJECT_CRS,
            transform=dst,
            nodata=np.nan,
            compress="deflate",
            tiled=True,
        ) as ds:
            ds.write(nd, 1)
            ds.update_tags(scenes=",".join(it["id"] for it in items))
    log.info("NDVI %s %d from %d scenes", cell, year, len(items))
    return out


def ndvi(cell: str, year: int) -> np.ndarray:
    """Cached NDVI of a cell as an analysis grid (row 0 = south, 1 m). Raises if not fetched."""
    import rasterio

    p = ndvi_path(cell, year)
    if not p.exists():
        raise FileNotFoundError(f"NDVI {year} of {cell} not cached; run data_src fetch-ancillary")
    with rasterio.open(p) as ds:
        return np.flipud(ds.read(1))
