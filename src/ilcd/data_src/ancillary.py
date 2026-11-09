"""Ancillary layers per cell, cached under cache/ancillary/ (raw inputs, not contracts).

ndvi(cell, year)  NAIP NDVI on the cell's 1 m grid (Microsoft Planetary Computer; old naip_ndvi.py), every scene
                  touching the cell mosaicked in STAC order; NaN where no NAIP covers it. Row 0 = south.
ortho(bounds, source, px, key)
                  RGB orthophoto of a square in EPSG:6345 as a north-up JPEG (old site_imagery.py): source
                  "in2011" (IndianaMap statewide orthophotos 2011, leaf-off) or "naip<year>".
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
    return _naip_search(grid.cell_bounds(cell), year)


def _naip_search(bounds, year: int) -> list[dict]:
    xmin, ymin, xmax, ymax = bounds
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


IN2011 = "https://di-ingov.img.arcgis.com/arcgis/rest/services/DynamicWebMercator/Indiana_2011_Imagery/ImageServer"
IN2011_INFO = "IndianaMap statewide orthophotos 2011, leaf-off, 12 in"


def ortho_path(source: str, key: str):
    return paths.cache_ancillary(f"ortho/{source}") / f"{key}.jpg"


def ortho(bounds, source: str, px: int, key: str):
    """(path, description) of the cached orthophoto of a square (xmin, ymin, xmax, ymax in EPSG:6345), px x px,
    north up; fetched on first use. The description names the source and, for NAIP, the image dates."""
    import io as _io

    from PIL import Image

    out = ortho_path(source, key)
    info = out.with_suffix(".txt")
    if out.exists() and info.exists():
        return out, info.read_text().strip()
    if source == "in2011":
        q = {
            "bbox": ",".join(f"{v:.2f}" for v in bounds),
            "bboxSR": crs.PROJECT_EPSG,
            "imageSR": crs.PROJECT_EPSG,
            "size": f"{px},{px}",
            "format": "jpg",
            "bandIds": "0,1,2",
            "f": "image",
        }
        r = http.session().get(f"{IN2011}/exportImage", params=q, timeout=120)
        r.raise_for_status()
        img, desc = Image.open(_io.BytesIO(r.content)).convert("RGB"), IN2011_INFO
    elif source.startswith("naip"):
        a, dates = _naip_rgb(bounds, int(source[4:]), px)
        img, desc = Image.fromarray(a), f"NAIP {', '.join(dates)}, leaf-on, 0.6 m"
    else:
        raise ValueError(f"unknown orthophoto source {source!r}")
    with atomic_path(out) as tmp:
        img.save(tmp, format="JPEG", quality=72)
    with atomic_path(info) as tmp:
        tmp.write_text(desc + "\n")
    return out, desc


def _naip_rgb(bounds, year: int, px: int):
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject

    items = _naip_search(bounds, year)
    if not items:
        raise RuntimeError(f"no NAIP {year} over {bounds}")
    token = _json("GET", TOKEN)["token"]
    dst = np.zeros((3, px, px), np.uint8)
    t = from_bounds(*bounds, px, px)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        for it in items:
            with rasterio.open(f"{it['assets']['image']['href']}?{token}") as src:
                for b in range(3):
                    reproject(
                        rasterio.band(src, b + 1),
                        dst[b],
                        src_nodata=0,
                        dst_transform=t,
                        dst_crs=crs.PROJECT_CRS,
                        dst_nodata=0,
                        init_dest_nodata=False,
                        resampling=Resampling.bilinear,
                    )
    return dst.transpose(1, 2, 0), sorted({it["properties"]["datetime"][:10] for it in items})


def ndvi(cell: str, year: int) -> np.ndarray:
    """Cached NDVI of a cell as an analysis grid (row 0 = south, 1 m). Raises if not fetched."""
    import rasterio

    p = ndvi_path(cell, year)
    if not p.exists():
        raise FileNotFoundError(f"NDVI {year} of {cell} not cached; run data_src fetch-ancillary")
    with rasterio.open(p) as ds:
        return np.flipud(ds.read(1))
