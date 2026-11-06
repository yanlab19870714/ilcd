"""Region work areas and their selection of files and cells (docs/design.md section 6.2)."""

import json

import geopandas as gpd
import pyarrow as pa
from shapely.geometry import box, shape
from shapely.ops import unary_union

from ..common import config, contracts, crs, grid, io, paths
from ..common.log import get_logger
from . import catalog, http

log = get_logger(__name__)


def counties(params) -> gpd.GeoDataFrame:
    """Census cartographic-boundary counties of the state (data_src.state_fips), in EPSG:6345, indexed by NAME."""
    url = params["data_src.counties"]
    z = paths.cache_ancillary("census") / url.rsplit("/", 1)[1]
    if not z.exists():
        http.download(url, z)
    g = gpd.read_file(f"zip://{z}")
    g = g[g["STATEFP"] == params["data_src.state_fips"]].to_crs(crs.PROJECT_CRS)
    return g.set_index("NAME")[["geometry"]]


def footprint(region: config.Region):
    """The region's own polygon (EPSG:6345), without buffer."""
    fp = region.footprint
    if "county" in fp:
        names = [fp["county"]] if isinstance(fp["county"], str) else list(fp["county"])
        c = counties(region.params)
        missing = [n for n in names if n not in c.index]
        if missing:
            raise ValueError(f"region {region.name}: no county {missing} (names as in the Census file)")
        return unary_union(list(c.loc[names, "geometry"]))
    with open(paths.config_dir() / fp["polygon"]) as f:
        gj = json.load(f)
    geoms = [shape(feat["geometry"]) for feat in gj["features"]]
    g = gpd.GeoSeries(geoms, crs=crs.GEOGRAPHIC).to_crs(crs.PROJECT_CRS)
    return unary_union(list(g))


def work_area(region: config.Region):
    return footprint(region).buffer(region.params["data_src.region_buffer_m"])


def select(region: config.Region) -> pa.Table:
    """Cells sharing area with the work area, and every file intersecting the work area or read by those cells;
    in_core marks units touching the footprint itself."""
    core = footprint(region)
    work = core.buffer(region.params["data_src.region_buffer_m"])
    lf = catalog.read_legacy_files()
    only = region.params["data_src.only_deliveries"]
    if only:
        unknown = sorted(set(only) - set(lf["delivery_id"]))
        if unknown:
            raise ValueError(f"region {region.name}: unknown deliveries {unknown}")
        lf = lf[lf["delivery_id"].isin(only)]
    cells = grid.cells_for_geometry(work)
    # every file the region's cells read (cells padded by data_src.points.read_pad_m), so that the scope's passes
    # cover every observation its cells make
    pad = region.params["data_src.points.read_pad_m"]
    reach = unary_union([box(*grid.cell_bounds(c)).buffer(pad, join_style="mitre") for c in cells])
    hit = lf[lf.intersects(work) | lf.intersects(reach)]
    rows = [
        {"unit": "file", "unit_id": f, "in_core": bool(g.intersects(core)), "delivery_id": d}
        for f, g, d in zip(hit["file_id"], hit.geometry, hit["delivery_id"], strict=True)
    ]
    for c in cells:
        rows.append(
            {
                "unit": "cell",
                "unit_id": c,
                "in_core": core.intersection(box(*grid.cell_bounds(c))).area > 0,
                "delivery_id": None,
            }
        )
    t = pa.Table.from_pylist(rows, schema=contracts.get("selection").arrow_schema())
    n_files = len(hit)
    log.info(
        "region %s: %d files (%d deliveries), %d cells",
        region.name,
        n_files,
        hit["delivery_id"].nunique(),
        len(rows) - n_files,
    )
    return t


def read_selection(scope: str, unit: str, core_only: bool = False) -> list[str]:
    t = io.read_table(paths.scope_dir(scope) / "data_src" / "selection.parquet", "selection").to_pandas()
    t = t[t["unit"] == unit]
    if core_only:
        t = t[t["in_core"]]
    return sorted(t["unit_id"])


def files_touching(cells: list[str], params) -> list[str]:
    """Every legacy file whose footprint overlaps one of the cells padded by data_src.points.read_pad_m: the files
    the per-cell steps of these cells read (whatever region asks)."""
    pad = params["data_src.points.read_pad_m"]
    lf = catalog.read_legacy_files()
    area = unary_union([box(*grid.cell_bounds(c)).buffer(pad, join_style="mitre") for c in cells])
    return sorted(lf[lf.intersects(area)]["file_id"])
