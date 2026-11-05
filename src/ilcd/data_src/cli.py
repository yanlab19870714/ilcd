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
        s.output(io.write_table(catalog.build_deliveries(params), out, "deliveries"), "deliveries")


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
    from . import regions

    for u in regions.read_selection(ids.scope(args.region), args.unit, args.core):
        print(u)


def _fetch_legacy(args):
    from . import fetch

    params = config.default_params()
    for f in _ids(args):
        fetch.fetch_legacy_step(f, params)


def _fetch_modern(args):
    from . import fetch

    params = config.default_params()
    for c in _ids(args):
        fetch.fetch_modern_step(c, params)


def _fetch_ancillary(args):
    from . import ancillary

    params = config.default_params()
    year = params["refs.ground.ndvi_year"]
    for c in _ids(args):
        with Step("data_src.fetch_ancillary", out_dir=paths.cell_dir(c), params={"ndvi_year": year}, unit=c) as s:
            s.input(ancillary.fetch_ndvi(c, year), hash_file=True)


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
    p.add_parser("deliveries", help="rebuild deliveries from headers, datum tests, overrides").set_defaults(
        fn=_deliveries
    )
    p.add_parser("headers", help="concatenate per-file legacy headers").set_defaults(fn=_headers)
    s = p.add_parser("select", help="files and cells of a region")
    s.add_argument("region")
    s.set_defaults(fn=_select)
    s = p.add_parser("list", help="print the file_ids or cell_ids of a region's selection")
    s.add_argument("region")
    s.add_argument("unit", choices=["file", "cell"])
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
        s.set_defaults(fn=fn)
    s = p.add_parser("datum", help="datum test of deliveries")
    s.add_argument("delivery", nargs="+")
    s.add_argument("--redecide", action="store_true", help="re-apply the decision rule to the existing samples")
    s.set_defaults(fn=_datum)
