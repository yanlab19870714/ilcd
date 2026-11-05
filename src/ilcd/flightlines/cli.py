"""`ilcd flightlines ...` commands."""

import pandas as pd
import pyarrow as pa

from ..common import config, ids, io, paths
from ..common.log import get_logger
from ..common.manifest import Step
from ..common.scope import files as scope_files
from ..data_src.cli import _ids

log = get_logger(__name__)


def _passes(args):
    from . import passes

    params = config.default_params()
    for f in _ids(args):
        passes.file_passes_step(f, params)


def _link(args):
    from . import link

    sc = ids.scope(None if args.region == "statewide" else args.region)
    params = config.default_params() if args.region == "statewide" else config.load_region(args.region).params
    out = paths.scope_dir(sc) / "flightlines"
    with Step("flightlines.link", out_dir=out, params=params.section("flightlines"), unit=sc) as step:
        tables, missing = [], []
        for f in scope_files(sc):
            p = paths.file_dir(f) / "passes.parquet"
            if not p.exists():
                missing.append(f)
                continue
            step.input(p, "passes")
            tables.append(io.read_table(p, "passes"))
        if missing:
            raise FileNotFoundError(f"{len(missing)} files of {sc} have no passes yet, e.g. {missing[:3]}")
        P = pa.concat_tables(tables)
        lines, members, blocks = link.build(P.to_pandas(), params.section("flightlines"))
        step.output(io.write_table(P, out / "passes.parquet", "passes"), "passes")
        step.output(io.write_table(lines, out / "lines.parquet", "lines"), "lines")
        step.output(io.write_table(members, out / "line_members.parquet", "line_members"), "line_members")
        step.output(io.write_table(blocks, out / "blocks.parquet", "blocks"), "blocks")
    report(P.to_pandas(), members.to_pandas(), lines.to_pandas())


def report(P: pd.DataFrame, members: pd.DataFrame, lines: pd.DataFrame):
    from . import link

    m = P.merge(members, on="pass_id")
    print(
        f"{len(P)} passes -> {lines['line_id'].nunique()} lines in {lines['block_id'].nunique()} blocks; "
        f"passes per line median {lines['n_passes'].median():.0f}, single-pass lines {(lines['n_passes'] == 1).sum()}"
    )
    a, b, na, nb = link.purity(m["line_id"], m["delivery_id"] + ":" + m["psid_mode"].astype(str))
    print(f"vs point_source_id: purity lines-in-psid {a:.4f}, psid-in-lines {b:.4f} ({na} lines, {nb} psid values)")


def register(sub):
    p = sub.add_parser("flightlines", help="passes per file, lines and blocks per scope")
    p = p.add_subparsers(dest="sub", required=True)
    s = p.add_parser("passes", help="passes of legacy files")
    s.add_argument("ids", nargs="*")
    s.add_argument("--from-file")
    s.add_argument("--chunk")
    s.set_defaults(fn=_passes)
    s = p.add_parser("link", help="lines and blocks of a region (or 'statewide')")
    s.add_argument("region")
    s.set_defaults(fn=_link)
