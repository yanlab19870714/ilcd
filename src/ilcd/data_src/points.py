"""The only reader of raw point clouds (docs/design.md sections 2 and 6.3).

Both readers return a dict of equal-length numpy arrays in the project frame:
  x, y, z     EPSG:6345 metres; NAVD88 / GEOID12B metres (legacy points without a valid GPS time are dropped)
  t           GPS time in the points' time domain (adjusted standard if known, else week seconds)
  scan_deg    scan angle, degrees
  cls         classification
  src         point_source_id
  rn, nr      return number, number of returns
  part        index into the returned `parts` list (legacy: file_ids; modern: batches)
and the list under key `parts`. Noise classes and gross outliers are removed (data_src.points).

Frames:
  legacy  lon/lat as stored by OT. Datum `itrf`: ITRF2014 -> NAD83(2011) at data_src.datum.itrf_epoch, then UTM 16N.
          Datum `nad83`: lon/lat taken as NAD83, projected only. Heights: US survey feet NAVD88/GEOID09 -> metres,
          then GEOID09 -> GEOID12B with the NOAA grids.
  modern  EPT EPSG:3857 -> lon/lat with the same null datum shift USGS used to build it -> UTM 16N; heights as stored.
"""

from functools import cache

import laspy
import numpy as np
from pyproj import Transformer

from ..common import crs, gpstime, grid
from ..common.log import get_logger
from . import catalog, fetch

log = get_logger(__name__)
FIELDS = ("x", "y", "z", "t", "scan_deg", "cls", "src", "rn", "nr", "part")


class DatumUndecided(RuntimeError):
    """A delivery has no datum decision yet (run data_src datum, or add an override)."""


class MissingCache(RuntimeError):
    """A raw file is not in the cache; fetch it first (reading never downloads)."""


# transforms --------------------------------------------------------------------------------------------------------


@cache
def _itrf_to_nad83() -> Transformer:
    return Transformer.from_crs("EPSG:7912", "EPSG:6318", always_xy=True)


@cache
def _geoid(grid_file: str) -> Transformer:
    """lon/lat/H -> lon/lat/(H + N): adds the geoid undulation of a NOAA grid."""
    return Transformer.from_pipeline(
        "+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad "
        f"+step +proj=vgridshift +grids={grid_file} +multiplier=1 +step +proj=unitconvert +xy_in=rad +xy_out=deg"
    )


def geoid_undulation(lon, lat, grid_file: str) -> np.ndarray:
    _, _, n = _geoid(grid_file).transform(np.asarray(lon, float), np.asarray(lat, float), np.zeros(np.size(lon)))
    n = np.asarray(n, float)
    if not np.all(np.isfinite(n)):
        raise ValueError(f"{grid_file} has no value at some points (grid missing from PROJ data?)")
    return n


def legacy_lonlat_to_project(lon, lat, datum: str, epoch: float):
    """(x, y) in EPSG:6345 and the NAD83 lon/lat used for the geoid lookup."""
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    if datum == "itrf":
        h, t = np.full_like(lon, 200.0), np.full_like(lon, epoch)
        lon, lat, _, _ = _itrf_to_nad83().transform(lon, lat, h, t)
    elif datum != "nad83":
        raise ValueError(f"datum must be itrf or nad83, not {datum!r}")
    x, y = crs.transformer("EPSG:6318", crs.PROJECT_CRS).transform(lon, lat)
    return np.asarray(x), np.asarray(y), lon, lat


def modern_merc_to_project(mx, my):
    lon, lat = crs.transformer("EPSG:3857", "EPSG:4326").transform(mx, my)
    x, y = crs.transformer("EPSG:6318", crs.PROJECT_CRS).transform(lon, lat)
    return np.asarray(x), np.asarray(y)


def _scan_deg(las) -> np.ndarray:
    names = las.point_format.dimension_names
    return np.asarray(las.scan_angle_rank if "scan_angle_rank" in names else las.scan_angle * 0.006, np.float32)


def _clean(d: dict, params) -> np.ndarray:
    """Mask without noise classes and gross z outliers relative to this part's ground (old pair_diagnostics.load)."""
    keep = ~np.isin(d["cls"], params["data_src.points.noise_classes"])
    g = d["z"][keep & (d["cls"] == 2)]
    if g.size >= 100:
        lo = np.percentile(g, 0.1) - params["data_src.points.z_below_ground_m"]
        hi = np.percentile(g, 99.9) + params["data_src.points.z_above_ground_m"]
        keep &= (d["z"] > lo) & (d["z"] < hi)
    return keep


def _concat(parts: list[dict], names: list) -> dict:
    if not parts:
        out = {k: np.empty(0) for k in FIELDS}
    else:
        out = {k: np.concatenate([p[k] for p in parts]) for k in FIELDS}
    out["parts"] = names
    return out


def _crop(d: dict, bounds) -> dict:
    if bounds is None:
        return d
    xmin, ymin, xmax, ymax = bounds
    m = (d["x"] >= xmin) & (d["x"] < xmax) & (d["y"] >= ymin) & (d["y"] < ymax)
    return {k: v[m] for k, v in d.items()}


# legacy ------------------------------------------------------------------------------------------------------------


def read_legacy_file(file_id: str, params, datum: str | None = None, bounds=None, part: int = 0) -> dict:
    """Points of one legacy file. `datum` overrides the delivery decision (used by the datum test only)."""
    row = catalog.legacy_file(file_id)
    path = fetch.legacy_path(file_id)
    if not path.is_file():
        raise MissingCache(f"{file_id} is not cached ({path}); run data_src fetch-legacy")
    dl = catalog.read_deliveries().loc[row["delivery_id"]]
    if datum is None:
        datum = dl["datum"]
        if datum is None or (isinstance(datum, float) and np.isnan(datum)):
            raise DatumUndecided(f"delivery {row['delivery_id']} has no datum decision")
    las = laspy.read(path)
    x, y, lon, lat = legacy_lonlat_to_project(las.x, las.y, datum, params["data_src.datum.itrf_epoch"])
    z = np.asarray(las.z, float) * crs.US_SURVEY_FOOT_M
    z = (
        z
        + geoid_undulation(lon, lat, params["data_src.geoid.legacy_grid"])
        - geoid_undulation(lon, lat, params["data_src.geoid.project_grid"])
    )
    t = np.asarray(las.gps_time, float)
    bit = fetch.adjusted_bit(las, row["delivery_id"])
    ttype, _ = gpstime.classify_points(bit, t, params["data_src.points.max_invalid_time_share"])
    time_ok = gpstime.valid(bit, t)
    if ttype == "week_seconds" and _known_week(dl):
        t = gpstime.week_to_adjusted(int(dl["gps_week"]), t)
    d = {
        "x": x,
        "y": y,
        "z": z,
        "t": t,
        "scan_deg": _scan_deg(las),
        "cls": np.asarray(las.classification, np.uint8),
        "src": np.asarray(las.point_source_id, np.uint16),
        "rn": np.asarray(las.return_number, np.uint8),
        "nr": np.asarray(las.number_of_returns, np.uint8),
        "part": np.full(len(x), part, np.int32),
    }
    keep = (
        _clean(d, params)
        & time_ok
        & _in_footprint(x, y, row.geometry.bounds, params["data_src.points.footprint_pad_m"])
    )
    return _crop({k: v[keep] for k, v in d.items()}, bounds)


def _in_footprint(x, y, fb, pad: float) -> np.ndarray:
    """Points within the file's footprint box (project CRS) + pad; the rest are stray coordinates."""
    return (x >= fb[0] - pad) & (x <= fb[2] + pad) & (y >= fb[1] - pad) & (y <= fb[3] + pad)


def _known_week(dl) -> bool:
    w = dl["gps_week"]
    return w is not None and not (isinstance(w, float) and np.isnan(w))


def legacy_files_for(cell: str, params) -> list[str]:
    from shapely.geometry import box

    pad = params["data_src.points.read_pad_m"]
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    lf = catalog.read_legacy_files()
    return sorted(lf[lf.intersects(box(xmin - pad, ymin - pad, xmax + pad, ymax + pad))]["file_id"])


def read_legacy(cell: str, params, pad: float | None = None) -> dict:
    """2011 points of every legacy file overlapping a cell (plus pad), cropped to the padded cell."""
    pad = params["data_src.points.read_pad_m"] if pad is None else pad
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    b = (xmin - pad, ymin - pad, xmax + pad, ymax + pad)
    files = legacy_files_for(cell, params)
    parts = [read_legacy_file(f, params, bounds=b, part=i) for i, f in enumerate(files)]
    return _concat(parts, files)


# modern ------------------------------------------------------------------------------------------------------------


def read_modern(cell: str, params, pad: float | None = None, primary_only: bool = True) -> dict:
    """3DEP points around a cell (from the cell cache), cropped to the padded cell. With primary_only (default),
    only the reference batch with the most ground points in the cell (docs/design.md section 6.2)."""
    pad = params["data_src.points.read_pad_m"] if pad is None else pad
    xmin, ymin, xmax, ymax = grid.cell_bounds(cell)
    b = (xmin - pad, ymin - pad, xmax + pad, ymax + pad)
    parts, names = [], []
    from ..common import paths

    for batch in fetch.modern_batches_for(cell):
        p = paths.cache_modern(batch, cell)
        if p.with_suffix(".empty").is_file():
            continue
        if not p.is_file():
            raise MissingCache(f"3DEP {batch} for {cell} is not cached; run data_src fetch-modern")
        las = laspy.read(p)
        x, y = modern_merc_to_project(las.x, las.y)
        d = {
            "x": x,
            "y": y,
            "z": np.asarray(las.z, float),
            "t": np.asarray(las.gps_time, float),
            "scan_deg": _scan_deg(las),
            "cls": np.asarray(las.classification, np.uint8),
            "src": np.asarray(las.point_source_id, np.uint16),
            "rn": np.asarray(las.return_number, np.uint8),
            "nr": np.asarray(las.number_of_returns, np.uint8),
            "part": np.full(len(x), len(names), np.int32),
        }
        keep = _clean(d, params)
        parts.append(_crop({k: v[keep] for k, v in d.items()}, b))
        names.append(batch)
    out = _concat(parts, names)
    if primary_only and len(names) > 1:
        n_ground = np.bincount(out["part"][out["cls"] == 2], minlength=len(names))
        k = int(np.argmax(n_ground))
        m = out["part"] == k
        out = {f: out[f][m] for f in FIELDS} | {"parts": [names[k]]}
        out["part"][:] = 0
    return out
