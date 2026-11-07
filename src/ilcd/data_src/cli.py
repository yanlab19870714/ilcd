"""`ilcd data_src ...` commands."""

from ..common import config, ids, io, paths
from ..common.log import get_logger
from ..common.manifest import Step

log = get_logger(__name__)


def _catalog(args):
    from . import catalog

    params = config.default_params()
    with Step("data_src.catalog", out_dir=paths.catalog("legacy_files").parent, params=params.section("data_src")) as s:
        for name, p in catalog.build(params).items():
            s.output(p, name)
    print(catalog.summary(catalog.read_legacy_files()))


def _deliveries(args):
    from . import catalog

    params = config.default_params()
    out = paths.catalog("deliveries")
    with Step("data_src.deliveries", out_dir=out.parent, params={}) as s:
        t = catalog.build_deliveries(params)
        undecided = [r["delivery_id"] for r in t.to_pylist() if r["gps_time_type"] is None or r["datum"] is None]
        if undecided and not args.partial:
            raise SystemExit(
                f"{len(undecided)} deliveries lack the headers of all their files or a datum decision "
                f"(run the statewide catalog stage, docs/runbook.md): {undecided[:10]}"
            )
        s.output(io.write_table(t, out, "deliveries"), "deliveries")


def _headers(args):
    from . import catalog

    out = paths.catalog("legacy_headers")
    with Step("data_src.headers", out_dir=out.parent, params={}) as s:
        t = catalog.build_headers()
        s.output(io.write_table(t, out, "legacy_headers"), "legacy_headers")
    print(f"{t.num_rows} files")


def _select(args):
    from . import regions

    r = config.load_region(args.region)
    out = paths.scope_dir(r.scope) / "data_src" / "selection.parquet"
    with Step("data_src.select", out_dir=out.parent, params=r.params.section("data_src"), unit=r.scope) as s:
        s.input(paths.catalog("legacy_files"), "legacy_files")
        s.output(io.write_table(regions.select(r), out, "selection"), "selection")


def _list(args):
    from . import catalog, regions

    if args.region == ids.STATEWIDE:
        if args.unit == "cell" or args.core:
            raise SystemExit("statewide lists files or deliveries")
        lf = catalog.read_legacy_files()
        units = sorted(lf["file_id"] if args.unit == "file" else lf["delivery_id"].unique())
    elif args.unit == "delivery":
        raise SystemExit("deliveries are listed statewide only")
    else:
        units = regions.read_selection(ids.scope(args.region), args.unit, args.core)
    for u in units:
        print(u)


def _todo(ids, args, out_dir, step, params) -> list[str]:
    """ids, minus those already done when --skip-existing is given. With params (the dict the step records, or a
    function of the id returning it), a unit is done when its manifest has these params and unchanged inputs
    (manifest.current); with None, when it has a manifest at all: for downloads into the raw caches, which depend on
    no parameter or upstream output."""
    if not getattr(args, "skip_existing", False):
        return ids
    from ..common.manifest import current, done

    if params is None:
        return [i for i in ids if not done(out_dir(i), step)]
    of = params if callable(params) else lambda _: params
    return [i for i in ids if not current(out_dir(i), step, of(i))]


def _each(ids, fn):
    """fn for every id; a failing unit is logged and the others still run, then the command fails naming them. A
    download chunk thus fetches all it can, and the failures are diagnosed together."""
    failed = []
    for i in ids:
        try:
            fn(i)
        except Exception:
            log.exception("%s failed", i)
            failed.append(i)
    if failed:
        raise SystemExit(f"{len(failed)} of {len(ids)} units failed: {failed[:20]}")


def _fetch_legacy(args):
    from . import fetch

    params = config.default_params()
    _each(
        _todo(_ids(args), args, paths.file_dir, "data_src.fetch_legacy", None),
        lambda f: fetch.fetch_legacy_step(f, params),
    )


def _fetch_modern(args):
    from . import fetch

    params = config.default_params()
    _each(
        _todo(_ids(args), args, paths.cell_dir, "data_src.fetch_modern", None),
        lambda c: fetch.fetch_modern_step(c, params),
    )


def _fetch_ancillary(args):
    from . import ancillary

    params = config.default_params()
    sp = {"ndvi_year": params["refs.ground.ndvi_year"]}
    for c in _todo(_ids(args), args, paths.cell_dir, "data_src.fetch_ancillary", sp):
        with Step("data_src.fetch_ancillary", out_dir=paths.cell_dir(c), params=sp, unit=c) as s:
            s.input(ancillary.fetch_ndvi(c, sp["ndvi_year"]), hash_file=True)


def _osm(args):
    from . import osm

    params = config.default_params()
    with Step("data_src.osm", out_dir=paths.cache_ancillary("osm"), params=params.section("data_src.osm")) as s:
        out = osm.build(params)
        for f in sorted(out.glob("*.parquet")) + [out / "snapshot.json"]:
            s.output(f)
    print(out)


def _cdl(args):
    from . import cdl

    params = config.default_params()
    with Step("data_src.cdl", out_dir=paths.cache_ancillary("cdl"), params={}) as s:
        for y in params["refs.screen.cdl_years"]:
            s.output(cdl.fetch(y, params["data_src.state_fips"]))


def _datum_report(args):
    from . import catalog, datum

    params = config.default_params()
    t = datum.report(params, samples=args.samples)
    if not args.samples:
        decided = set(t["delivery"]) if len(t) else set()
        d = catalog.read_deliveries()
        missing = sorted(set(d.index) - decided)
        print(t.to_string(index=False) if len(t) else "no datum tests yet")
        print(f"\n{len(decided)} deliveries tested, {len(missing)} not tested yet")
    else:
        print(t.to_string(index=False))


def _datum(args):
    from . import datum

    params = config.default_params()
    for d in args.delivery:
        doc = datum.redecide(d, params) if args.redecide else datum.test_delivery(d, params)
        print(d, doc["decision"])


def _ids(args) -> list[str]:
    """IDs from the command line, or from a file (one per line) with lines start..end (1-based, for job arrays)."""
    if args.from_file is None:
        return args.ids
    with open(args.from_file) as f:
        lines = [s.strip() for s in f if s.strip()]
    if args.chunk is not None:
        k, n = map(int, args.chunk.split("/"))
        lines = lines[k - 1 :: n]
    return lines


def register(sub):
    p = sub.add_parser("data_src", help="catalogs, downloads, datum, selection").add_subparsers(
        dest="sub", required=True
    )
    p.add_parser("catalog", help="build legacy_files, deliveries, modern_batches").set_defaults(fn=_catalog)
    s = p.add_parser("deliveries", help="rebuild deliveries from headers, datum tests, overrides")
    s.add_argument("--partial", action="store_true", help="write even if some deliveries are still undecided")
    s.set_defaults(fn=_deliveries)
    p.add_parser("headers", help="concatenate per-file legacy headers").set_defaults(fn=_headers)
    s = p.add_parser("select", help="files and cells of a region")
    s.add_argument("region")
    s.set_defaults(fn=_select)
    s = p.add_parser(
        "list", help="print a region's file_ids or cell_ids, or (region `statewide`) all file_ids or deliveries"
    )
    s.add_argument("region")
    s.add_argument("unit", choices=["file", "cell", "delivery"])
    s.add_argument("--core", action="store_true", help="only units inside the footprint itself")
    s.set_defaults(fn=_list)
    for name, fn, what in (
        ("fetch-legacy", _fetch_legacy, "file_ids"),
        ("fetch-modern", _fetch_modern, "cell_ids"),
        ("fetch-ancillary", _fetch_ancillary, "cell_ids"),
    ):
        s = p.add_parser(name, help=f"fill the cache for {what}")
        s.add_argument("ids", nargs="*")
        s.add_argument("--from-file", help=f"file with one of {what} per line")
        s.add_argument("--chunk", help="k/n: every n-th line starting at line k (job arrays)")
        s.add_argument("--skip-existing", action="store_true", help="skip units that already have a manifest")
        s.set_defaults(fn=fn)
    s = p.add_parser("datum", help="datum test of deliveries")
    s.add_argument("delivery", nargs="+")
    s.add_argument("--redecide", action="store_true", help="re-apply the decision rule to the existing samples")
    s.set_defaults(fn=_datum)
    p.add_parser("cdl", help="USDA Cropland Data Layer of the state for refs.screen.cdl_years").set_defaults(fn=_cdl)
    p.add_parser("osm", help="statewide OSM roads, buildings and water from the Geofabrik extract").set_defaults(
        fn=_osm
    )
    s = p.add_parser("datum-report", help="summary of all datum tests, for review")
    s.add_argument("--samples", action="store_true", help="one row per sample instead of per delivery")
    s.set_defaults(fn=_datum_report)
