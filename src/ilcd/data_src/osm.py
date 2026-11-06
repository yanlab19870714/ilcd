"""OpenStreetMap layers for the whole state, from one Geofabrik extract (no per-cell API calls, no rate limits).

`build` downloads the extract (data_src.osm.url), and writes three GeoParquet layers in EPSG:6345 under
cache/ancillary/osm/<snapshot date>/ plus snapshot.json (source URL, Last-Modified, sha256):
  roads      OSM ways with a highway tag (osm_id, highway)
  buildings  multipolygons with a building tag (osm_id, building)
  water      multipolygons tagged natural=water, and waterway lines (river, stream, canal, drain) (osm_id, kind)
`layer(name)` returns the newest snapshot's layer (cached per process). OSM is current, not 2011 or 2018: roads and
large water bodies rarely move, which is all refs uses it for (road labels, distance to water).
"""

import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import geopandas as gpd
import pandas as pd

from ..common import crs, io, paths
from ..common.log import get_logger
from . import http

log = get_logger(__name__)
WATERWAYS = ("river", "stream", "canal", "drain")


def _root() -> Path:
    return paths.cache_ancillary("osm")


def snapshot_dir() -> Path:
    snaps = sorted(p for p in _root().glob("*") if (p / "snapshot.json").exists())
    if not snaps:
        raise FileNotFoundError("no OSM snapshot; run `ilcd data_src osm`")
    return snaps[-1]


def build(params) -> Path:
    import pyogrio

    url = params["data_src.osm.url"]
    head = http.session().head(url, allow_redirects=True, timeout=60)
    head.raise_for_status()
    modified = datetime.strptime(head.headers["Last-Modified"], "%a, %d %b %Y %H:%M:%S %Z")
    out = _root() / modified.strftime("%Y-%m-%d")
    if (out / "snapshot.json").exists():
        log.info("OSM snapshot %s already built", out.name)
        return out
    pbf = out / "extract.osm.pbf"
    if not pbf.exists():
        http.download(url, pbf, expected_size=int(head.headers["Content-Length"]))

    def read(layer, where, columns):
        g = pyogrio.read_dataframe(pbf, layer=layer, where=where, columns=columns)
        return g.set_crs(crs.GEOGRAPHIC, allow_override=True).to_crs(crs.PROJECT_CRS)

    roads = read("lines", "highway IS NOT NULL", ["osm_id", "highway"])
    buildings = read("multipolygons", "building IS NOT NULL", ["osm_id", "osm_way_id", "building"])
    buildings["osm_id"] = buildings["osm_id"].fillna(buildings["osm_way_id"])
    wpoly = read("multipolygons", "natural = 'water'", ["osm_id", "osm_way_id"])
    wpoly["osm_id"] = wpoly["osm_id"].fillna(wpoly["osm_way_id"])
    wline = read("lines", "waterway IN ('" + "','".join(WATERWAYS) + "')", ["osm_id", "waterway"])
    water = gpd.GeoDataFrame(
        pd.concat(
            [
                wpoly.assign(kind="water_body")[["osm_id", "kind", "geometry"]],
                wline.rename(columns={"waterway": "kind"})[["osm_id", "kind", "geometry"]],
            ],
            ignore_index=True,
        ),
        geometry="geometry",
        crs=crs.PROJECT_CRS,
    )
    for name, g in (("roads", roads), ("buildings", buildings[["osm_id", "building", "geometry"]]), ("water", water)):
        with io.atomic_path(out / f"{name}.parquet") as tmp:
            g.to_parquet(tmp)
        log.info("OSM %s: %d features", name, len(g))
    doc = {
        "url": url,
        "last_modified_utc": modified.replace(tzinfo=UTC).isoformat(),
        "sha256": io.sha256_file(pbf),
        "counts": {"roads": len(roads), "buildings": len(buildings), "water": len(water)},
    }
    with io.atomic_path(out / "snapshot.json") as tmp:
        tmp.write_text(json.dumps(doc, indent=1) + "\n")
    return out


@cache
def _layer(path: str) -> gpd.GeoDataFrame:
    g = gpd.read_parquet(path)
    g.sindex  # noqa: B018  (build the spatial index once)
    return g


def layer(name: str, bounds=None) -> gpd.GeoDataFrame:
    """A layer of the newest snapshot, optionally only features intersecting bounds (xmin, ymin, xmax, ymax)."""
    g = _layer(str(snapshot_dir() / f"{name}.parquet"))
    if bounds is None:
        return g
    return g.cx[bounds[0] : bounds[2], bounds[1] : bounds[3]]
