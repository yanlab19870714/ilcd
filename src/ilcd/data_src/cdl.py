"""USDA Cropland Data Layer (30 m), one statewide GeoTIFF per year from CropScape, cached under cache/ancillary/cdl/.

crop_share(G, cell, years) gives, on an analysis grid, 1 where any of the years shows a cultivated class (CDL codes
1-61, 66-77, 204-254), else 0; pasture/grassland (62, 176), forest, water, developed and wetlands are not crops.
Tillage changes the surface by centimetres, so cultivated land is no stable reference.
"""

import re
from functools import cache

import numpy as np

from ..common import crs, paths
from ..common.log import get_logger
from . import http

log = get_logger(__name__)
SERVICE = "https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile"
CROPS = np.zeros(256, bool)
CROPS[1:62] = True
CROPS[66:78] = True
CROPS[204:255] = True
CROPS[62] = False  # pasture/grass


def path(year: int, fips: str):
    return paths.cache_ancillary("cdl") / f"CDL_{year}_{fips}.tif"


def fetch(year: int, fips: str):
    p = path(year, fips)
    if p.exists():
        return p
    r = http.session().get(SERVICE, params={"year": year, "fips": fips}, timeout=600)
    r.raise_for_status()
    m = re.search(r"<returnURL>(.+?)</returnURL>", r.text)
    if m is None:
        raise RuntimeError(f"CropScape gave no file for {year}/{fips}: {r.text[:200]}")
    http.download(m.group(1), p)
    return p


@cache
def _dataset(p: str):
    import rasterio

    return rasterio.open(p)


def crop_mask(G, years: list[int], fips: str) -> np.ndarray:
    """Boolean grid (row 0 = south) on analysis grid G: cultivated in any of the years (nearest 30 m pixel)."""
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject

    out = np.zeros((G.ny, G.nx), bool)
    dst = Affine(G.res, 0, G.x0, 0, -G.res, G.y0 + G.ny * G.res)  # north-up for reproject
    for y in years:
        p = path(y, fips)
        if not p.exists():
            raise FileNotFoundError(f"CDL {y} not cached; run `ilcd data_src cdl`")
        ds = _dataset(str(p))
        tmp = np.zeros((G.ny, G.nx), np.uint8)
        reproject(
            _band(str(p)),
            tmp,
            src_transform=ds.transform,
            src_crs=ds.crs,
            dst_transform=dst,
            dst_crs=crs.PROJECT_CRS,
            resampling=Resampling.nearest,
        )
        out |= CROPS[np.flipud(tmp)]
    return out


@cache
def _band(p: str) -> np.ndarray:
    """The whole state band, read once per process (about 130 MB for Indiana at uint8)."""
    return _dataset(p).read(1)
