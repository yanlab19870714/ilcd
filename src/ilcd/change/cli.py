"""`ilcd change ...` commands."""

from ..common import config, grid, ids, io, paths
from ..common.log import get_logger
from ..common.manifest import Step
from ..data_src.cli import _ids, _todo

log = get_logger(__name__)


def _scope(args):
    sc = ids.scope(None if args.scope == "statewide" else args.scope)
    params = config.default_params() if args.scope == "statewide" else config.load_region(args.scope).params
    return sc, params


def _site_params(site: str):
    """(scope, params) of the scope whose model corrects the site."""
    sc = config.site_scope(site)
    region = ids.parse_scope(sc)
    return sc, config.default_params() if region is None else config.load_region(region).params


def _dod(args):
    import numpy as np

    from ..align import method
    from ..data_src import points
    from . import dod

    sc, params = _scope(args)
    p = params.section("change.dod")
    saved = method.load(paths.model_dir(sc))
    status_path = paths.scope_dir(sc) / "eval" / "status.parquet"
    status = io.read_table(status_path, "status").to_pandas()
    base = paths.scope_dir(sc) / "change" / "dod"
    for cell in _todo(_ids(args), args, lambda c: base / c, "change.dod", p):
        out = base / cell
        with Step("change.dod", out_dir=out, params=p, unit=cell) as step:
            step.input(paths.model_dir(sc) / "model.json", "model")
            step.input(status_path, "status")
            leg = points.read_legacy(cell, params, pad=p["pad_m"])
            mod = points.read_modern(cell, params, pad=p["pad_m"])
            leg = method.apply(saved, leg)
            if mod["x"].size:
                mod["z"] = method.to_reference_batch(saved, mod["z"], mod["parts"][0])
            by_line, fallback = dod.sys_sd(status, cell)
            bands = dod.compute(cell, leg, mod, by_line, fallback, p["pad_m"], p)
            transform, _, _ = grid.pixel_grid(cell, p["res_m"])
            step.output(io.write_raster(bands, transform, out / "dod.tif", "dod"), "dod")
            if not np.isfinite(bands["dz_m"]).any():
                step.empty("no pixel with ground in both epochs")
        c = bands["cls"]
        log.info(
            "dod %s %s: %d pixels with dz, lowered %d, raised %d, lost to water %d, gained %d",
            sc,
            cell,
            int(np.isfinite(bands["dz_m"]).sum()),
            int((c == dod.LOWERED).sum()),
            int((c == dod.RAISED).sum()),
            int((c == dod.LOST_TO_WATER).sum()),
            int((c == dod.LAND_GAINED).sum()),
        )


def _m3c2(args):
    import numpy as np

    from ..align import method
    from ..data_src import points
    from . import m3c2

    sc, params = _site_params(args.site)
    p = params.section("change.m3c2")
    saved = method.load(paths.model_dir(sc))
    status_path = paths.scope_dir(sc) / "eval" / "status.parquet"
    status = io.read_table(status_path, "status").to_pandas()
    base = paths.site_dir(args.site) / "change" / "m3c2"
    sp = {"scope": sc, **p}
    for cell in _todo(_ids(args), args, lambda c: base / c, "change.m3c2", sp):
        out = base / cell
        with Step("change.m3c2", out_dir=out, params=sp, unit=cell) as step:
            step.input(paths.model_dir(sc) / "model.json", "model")
            step.input(status_path, "status")
            leg = method.apply(saved, points.read_legacy(cell, params, pad=p["pad_m"]))
            mod = points.read_modern(cell, params, pad=p["pad_m"])
            core = m3c2.core_points(cell, mod, p["core_res_m"])
            g = (leg["cls"] == 2) & leg["corrected"]
            if len(core) == 0 or not g.any():
                step.output(io.write_empty_table(out / "m3c2.parquet", "m3c2"), "m3c2")
                step.empty("no 3DEP ground or no corrected 2011 ground in the cell")
                continue
            mg = mod["cls"] == 2
            zm = method.to_reference_batch(saved, mod["z"][mg], mod["parts"][0])
            core[:, 2] = method.to_reference_batch(saved, core[:, 2], mod["parts"][0])
            reg = m3c2.registration_error(status, cell)
            t = m3c2.run(
                np.c_[leg["x"][g], leg["y"][g], leg["z"][g]], np.c_[mod["x"][mg], mod["y"][mg], zm], core, reg, p
            )
            step.output(io.write_table(t, out / "m3c2.parquet", "m3c2"), "m3c2")
        log.info(
            "m3c2 %s %s: %d core points, %d significant, registration error %.3f m",
            args.site,
            cell,
            len(t),
            int(t["sig"].sum()),
            reg,
        )


def _objects(args):
    import geopandas as gpd
    import pandas as pd

    from ..common.crs import PROJECT_CRS
    from ..data_src import osm
    from . import objects

    _, params = _site_params(args.site)
    p, pm = params.section("change.objects"), params.section("change.m3c2")
    out = paths.site_dir(args.site) / "change"
    geom = config.site_geometry(args.site)
    cells = grid.cells_for_geometry(geom)
    with Step("change.objects", out_dir=out, params={**p, "core_res_m": pm["core_res_m"]}, unit=args.site) as step:
        parts, n_cores = [], 0
        for c in cells:
            f = out / "m3c2" / c / "m3c2.parquet"
            if not f.exists():
                raise FileNotFoundError(f"{f}: run `ilcd change m3c2 {args.site}` for every site cell first")
            step.input(f, "m3c2")
            t = io.read_table(f, "m3c2", columns=["core_id", "x_m", "y_m", "d_m", "lod_m", "sig", "normal_z"])
            parts.append(objects.candidates(t.to_pandas(), p).assign(cell_id=c))
            n_cores += t.num_rows
        cores = pd.concat(parts, ignore_index=True)
        water = osm.layer("water", geom.buffer(params["refs.screen.water_search_m"]).bounds)
        obj, mem = objects.find(cores, args.site, pm["core_res_m"], water.geometry.values, p)
        gdf = gpd.GeoDataFrame(obj, geometry="geometry", crs=PROJECT_CRS)
        if len(gdf):
            step.output(io.write_table(gdf, out / "objects.parquet", "objects"), "objects")
        else:
            step.output(io.write_empty_table(out / "objects.parquet", "objects"), "objects")
            step.empty("no change object")
        step.output(io.write_table(mem, out / "object_members.parquet", "object_members"), "object_members")
    k = obj["kind"].value_counts().to_dict() if len(obj) else {}
    print(
        f"{args.site}: {len(obj)} objects {k} from {len(cores)} candidates among {n_cores} core points "
        f"in {len(cells)} cells"
    )


def _site_cells(args):
    """Cells of a site polygon (one per line), for --from-file."""
    for c in grid.cells_for_geometry(config.site_geometry(args.site)):
        print(c)


def register(sub):
    p = sub.add_parser("change", help="DoD, M3C2 and change objects").add_subparsers(dest="sub", required=True)
    s = p.add_parser("dod", help="DEM of difference of cells, with the model of a scope")
    s.add_argument("scope", help="region name or 'statewide'")
    s.add_argument("ids", nargs="*")
    s.add_argument("--from-file")
    s.add_argument("--chunk")
    s.add_argument("--skip-existing", action="store_true", help="skip cells that already have a DoD of this scope")
    s.set_defaults(fn=_dod)
    s = p.add_parser("m3c2", help="M3C2 of cells of a site, with the model of the site's scope")
    s.add_argument("site")
    s.add_argument("ids", nargs="*")
    s.add_argument("--from-file")
    s.add_argument("--chunk")
    s.add_argument("--skip-existing", action="store_true", help="skip cells that already have M3C2")
    s.set_defaults(fn=_m3c2)
    s = p.add_parser("objects", help="change objects of a site from its M3C2 cells")
    s.add_argument("site")
    s.set_defaults(fn=_objects)
    s = p.add_parser("site-cells", help="print the cells of a site")
    s.add_argument("site")
    s.set_defaults(fn=_site_cells)
