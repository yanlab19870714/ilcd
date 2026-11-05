"""`ilcd refs ...` commands."""

from ..common import config, ids, io, paths, scope
from ..common.log import get_logger
from ..common.manifest import Step
from ..data_src.cli import _ids

log = get_logger(__name__)


def _cell(args):
    from . import cell

    params = config.default_params()
    for c in _ids(args):
        cell.cell_step(c, params)


def _screen(args):
    from . import screen

    sc = ids.scope(None if args.region == "statewide" else args.region)
    params = config.default_params() if args.region == "statewide" else config.load_region(args.region).params
    out = paths.scope_dir(sc) / "refs"
    with Step("refs.screen", out_dir=out, params=params.section("refs.screen"), unit=sc) as step:
        patches = scope.gather_cells(sc, "patches", step).to_pandas()
        patches = patches.assign(x=0.0, y=0.0)
        if len(patches):
            import geopandas as gpd

            g = gpd.GeoSeries.from_wkb(patches["geometry"]).centroid
            patches["x"], patches["y"] = g.x.to_numpy(), g.y.to_numpy()
        obs = scope.gather_cells(sc, "obs", step).to_pandas()
        t = screen.screen(patches, obs, params.section("refs.screen"))
        step.output(io.write_table(t, out / "screen.parquet", "screen"), "screen")
    s = t.to_pandas()["reason"].value_counts().to_dict()
    print(f"{len(patches)} patches, {len(obs)} obs; screened: {s}")


def register(sub):
    p = sub.add_parser("refs", help="reference surfaces and observations").add_subparsers(dest="sub", required=True)
    s = p.add_parser("cell", help="patches, obs and overlap_obs of cells")
    s.add_argument("ids", nargs="*")
    s.add_argument("--from-file")
    s.add_argument("--chunk")
    s.set_defaults(fn=_cell)
    s = p.add_parser("screen", help="scope-level change screening of patches")
    s.add_argument("region")
    s.set_defaults(fn=_screen)
