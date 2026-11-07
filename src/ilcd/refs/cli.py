"""`ilcd refs ...` commands."""

from ..common import config, crs, ids, io, paths, scope
from ..common.log import get_logger
from ..common.manifest import Step
from ..data_src.cli import _ids

log = get_logger(__name__)


def _cell(args):
    from ..data_src.cli import _todo
    from . import cell

    params = config.default_params()
    for c in _todo(
        _ids(args), args, lambda c: paths.cell_dir(c) / "refs", "refs.cell", lambda c: cell.step_params(c, params)
    ):
        cell.cell_step(c, params)


def _screen(args):
    from . import screen

    sc = ids.scope(None if args.region == "statewide" else args.region)
    params = config.default_params() if args.region == "statewide" else config.load_region(args.region).params
    out = paths.scope_dir(sc) / "refs"
    with Step("refs.screen", out_dir=out, params=params.section("refs.screen"), unit=sc) as step:
        import geopandas as gpd

        t = scope.gather_cells(sc, "patches", step).to_pandas()
        patches = gpd.GeoDataFrame(
            t.drop(columns="geometry"), geometry=gpd.GeoSeries.from_wkb(t["geometry"]), crs=crs.PROJECT_CRS
        )
        c = patches.geometry.centroid
        patches["x"], patches["y"] = c.x.to_numpy(), c.y.to_numpy()
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
    s.add_argument("--skip-existing", action="store_true", help="skip cells that already have refs")
    s.set_defaults(fn=_cell)
    s = p.add_parser("screen", help="scope-level change screening of patches")
    s.add_argument("region")
    s.set_defaults(fn=_screen)
