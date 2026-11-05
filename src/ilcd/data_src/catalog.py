"""Statewide catalogs (docs/design.md section 6.1): legacy_files, deliveries, modern_batches.

legacy_files comes from the OT bucket listing (every LAZ and its size) and the OT tile index (footprints). Files of
the same name in several folders of one zone are one file_id; the canonical copy is the first folder alphabetically.
A file missing from the index gets its footprint from its LAS header (HTTP range read).

deliveries is derived: folders from the listing, time types from legacy_headers, datum decisions from datum.json,
manual facts from config/deliveries.yaml. Rebuild it whenever one of those changes.
"""

import json
import re
from collections import defaultdict
from functools import lru_cache

import geopandas as gpd
import pandas as pd
import pyarrow as pa
from shapely.geometry import box

from ..common import config, crs, ids, io, paths
from ..common.log import get_logger
from . import http

log = get_logger(__name__)
_CODE = re.compile(r"_(\d{8})_")
_YEAR = re.compile(r"^ot_in(\d{4})_")


def _bucket(params) -> tuple[str, str]:
    """(bucket base URL, key prefix) from data_src.legacy_bucket, e.g. (.../pc-bulk, IN_2011_2013/)."""
    url = params["data_src.legacy_bucket"].rstrip("/")
    base, prefix = url.rsplit("/", 1)
    return base, prefix + "/"


def _tile_index(params) -> gpd.GeoDataFrame:
    base, prefix = _bucket(params)
    z = paths.cache_ancillary("ot_tileindex") / f"{prefix.rstrip('/')}_TileIndex.zip"
    if not z.exists():
        http.download(f"{base}/{prefix}{z.name}", z)
    g = gpd.read_file(f"zip://{z}")
    g["key"] = g["URL"].str.split("/pc-bulk/", n=1).str[1]
    return g[["key", "geometry"]].set_crs(crs.GEOGRAPHIC, allow_override=True)


def build_legacy_files(params) -> gpd.GeoDataFrame:
    base, prefix = _bucket(params)
    listing = [(k, s) for k, s in http.list_bucket(base, prefix) if k.lower().endswith(".laz")]
    if not listing:
        raise RuntimeError(f"no LAZ files under {base}/{prefix}")
    index = _tile_index(params).drop_duplicates("key").set_index("key")["geometry"]
    groups = defaultdict(list)  # (zone_dir, filename) -> [(folder, key, size)]
    for key, size in listing:
        parts = key.split("/")
        zone_dir, folder, name = parts[1], "/".join(parts[2:-1]), parts[-1]
        groups[(zone_dir, name)].append((folder, key, size))
    rows = []
    for (zone_dir, name), copies in sorted(groups.items()):
        copies.sort()
        folder, key, size = copies[0]
        geom = next((index[k] for _, k, _ in copies if k in index.index), None)
        if geom is None:
            log.info("%s is not in the tile index; footprint from its header", key)
            geom = box(*http.las_header_bounds(f"{base}/{key}"))
        code, year = _CODE.search(name), _YEAR.match(name)
        if code is None or year is None:
            raise ValueError(f"unexpected legacy file name {name}")
        rows.append(
            {
                "file_id": ids.file_id(zone_dir, name),
                "zone": ids.ZONE_DIRS[zone_dir],
                "filename": name,
                "tile_code": code.group(1),
                "year_tag": int(year.group(1)),
                "delivery_id": ids.delivery_id(zone_dir, folder),
                "other_deliveries": [ids.delivery_id(zone_dir, f) for f, _, _ in copies[1:]],
                "url": f"{base}/{key}",
                "copy_sizes_differ": len({s for _, _, s in copies}) > 1,
                "size_bytes": size,
                "geometry": geom,
            }
        )
    g = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs.GEOGRAPHIC).to_crs(crs.PROJECT_CRS)
    log.info(
        "%d LAZ objects -> %d distinct files (%d listed in more than one folder)",
        len(listing),
        len(g),
        sum(len(r["other_deliveries"]) > 0 for r in rows),
    )
    return g


def build_modern_batches(params) -> gpd.GeoDataFrame:
    """Reference 3DEP batches (data_src.ept.reference_batches) whose EPT boundary intersects the state."""
    from .regions import counties

    p = paths.cache_ancillary("ept") / "resources.geojson"
    if not p.exists():
        http.download(params["data_src.ept_boundaries"], p)
    r = gpd.read_file(p).to_crs(crs.PROJECT_CRS)
    state = counties(params).union_all()
    r = r[r.intersects(state)].copy()
    keep = r["name"].astype(str).str.match(params["data_src.ept.reference_batches"])
    log.info("%d EPT resources intersect the state; %d are reference batches", len(r), int(keep.sum()))
    r = r[keep]
    if r.empty:
        raise RuntimeError("no EPT resource matches data_src.ept.reference_batches")
    base = params["data_src.ept.base_url"].rstrip("/")
    out = gpd.GeoDataFrame(
        {
            "batch": r["name"].astype(str),
            "ept_url": [f"{base}/{n}/ept.json" for n in r["name"]],
            "n_points": r["count"].astype("int64"),
            "geometry": r.geometry.values,
        },
        geometry="geometry",
        crs=crs.PROJECT_CRS,
    )
    return out.sort_values("batch").reset_index(drop=True)


def build_deliveries(params, legacy_files: gpd.GeoDataFrame | None = None) -> pa.Table:
    lf = legacy_files if legacy_files is not None else read_legacy_files()
    count = defaultdict(int)
    for d, others in zip(lf["delivery_id"], lf["other_deliveries"], strict=True):
        count[d] += 1
        for o in others:
            count[o] += 1
    canonical = defaultdict(list)
    for f, d in zip(lf["file_id"], lf["delivery_id"], strict=True):
        canonical[d].append(f)
    headers = None
    if paths.catalog("legacy_headers").exists():
        headers = io.read_table(paths.catalog("legacy_headers"), "legacy_headers").to_pandas().set_index("file_id")
    overrides = config.load_delivery_overrides()
    rows = []
    for d in sorted(count):
        zone_dir, folder = ids.parse_delivery(d)
        o = overrides.get(d, {})
        ttype = None
        if headers is not None:
            types = set(headers["gps_time_type"].reindex(canonical[d]).dropna())
            ttype = None if not types else types.pop() if len(types) == 1 else "mixed"
        week = o.get("gps_week")
        datum, source = _datum_decision(d, o)
        rows.append(
            {
                "delivery_id": d,
                "zone": ids.ZONE_DIRS[zone_dir],
                "folder": folder,
                "batch": folder.split("/")[0],
                "n_files": count[d],
                "gps_time_type": ttype,
                "gps_week": week,
                "time_domain": ids.time_domain(ttype, d, week)
                if ttype in ("adjusted_standard", "week_seconds")
                else None,
                "datum": datum,
                "datum_source": source,
                "psid_meaning": o.get("psid_meaning"),
            }
        )
    unknown = sorted(set(overrides) - set(count))
    if unknown:
        raise ValueError(f"config/deliveries.yaml names unknown deliveries {unknown}")
    return pa.Table.from_pylist(rows, schema=_contract("deliveries").arrow_schema())


def _datum_decision(delivery: str, override: dict) -> tuple[str | None, str | None]:
    if "datum" in override:
        return override["datum"], "override"
    p = paths.delivery_dir(delivery) / "datum.json"
    if p.exists():
        decision = io.read_json(p, "datum")["decision"]
        return (decision, "test") if decision != "ambiguous" else (None, None)
    return None, None


def _contract(name):
    from ..common import contracts

    return contracts.get(name)


def build(params) -> dict:
    """Build and write legacy_files, deliveries and modern_batches; returns the paths written."""
    lf = build_legacy_files(params)
    out = {"legacy_files": io.write_table(lf, paths.catalog("legacy_files"), "legacy_files")}
    out["deliveries"] = io.write_table(build_deliveries(params, lf), paths.catalog("deliveries"), "deliveries")
    out["modern_batches"] = io.write_table(
        build_modern_batches(params), paths.catalog("modern_batches"), "modern_batches"
    )
    return out


def build_headers() -> pa.Table:
    """Concatenate files/*/legacy_header.parquet into the statewide legacy_headers table."""
    parts = [
        io.read_table(p, "legacy_headers")
        for p in sorted((paths.data_root() / "files").glob("*/legacy_header.parquet"))
    ]
    c = _contract("legacy_headers")
    return pa.concat_tables(parts) if parts else c.empty_table()


# readers used by the other data_src parts -------------------------------------------------------------------------


def _mtime(name: str) -> int:
    return paths.catalog(name).stat().st_mtime_ns


@lru_cache(maxsize=4)
def _legacy_files(_mtime: int) -> gpd.GeoDataFrame:
    return io.read_vector(paths.catalog("legacy_files"), "legacy_files")


@lru_cache(maxsize=4)
def _deliveries(_mtime: int) -> pd.DataFrame:
    return io.read_table(paths.catalog("deliveries"), "deliveries").to_pandas().set_index("delivery_id")


@lru_cache(maxsize=4)
def _modern_batches(_mtime: int) -> gpd.GeoDataFrame:
    return io.read_vector(paths.catalog("modern_batches"), "modern_batches")


# Cached per process and re-read when the file changes; callers must not modify the returned frames.
def read_legacy_files() -> gpd.GeoDataFrame:
    return _legacy_files(_mtime("legacy_files"))


def read_deliveries() -> pd.DataFrame:
    return _deliveries(_mtime("deliveries"))


def read_modern_batches() -> gpd.GeoDataFrame:
    return _modern_batches(_mtime("modern_batches"))


def legacy_file(file_id: str) -> pd.Series:
    """The legacy_files row of one file."""
    lf = read_legacy_files()
    hit = lf.index[lf["file_id"] == file_id]
    if len(hit) != 1:
        raise KeyError(f"{file_id} is not in legacy_files")
    return lf.loc[hit[0]]


def summary(lf: gpd.GeoDataFrame) -> str:
    n = len(lf)
    return json.dumps(
        {
            "files": n,
            "with_copies": int((lf["other_deliveries"].str.len() > 0).sum()),
            "copy_sizes_differ": int(lf["copy_sizes_differ"].sum()),
            "deliveries": int(lf["delivery_id"].nunique()),
            "bytes": int(lf["size_bytes"].sum()),
        }
    )
