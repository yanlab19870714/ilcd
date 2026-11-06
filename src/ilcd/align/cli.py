"""`ilcd align ...` commands."""

from ..common import config, ids, io, paths, scope
from ..common.log import get_logger
from ..common.manifest import Step

log = get_logger(__name__)


def scope_params(region: str):
    sc = ids.scope(None if region == "statewide" else region)
    params = config.default_params() if region == "statewide" else config.load_region(region).params
    return sc, params


def _fit(args):
    from . import inputs, method

    sc, params = scope_params(args.region)
    out = paths.model_dir(sc)
    p = params.section("align")
    with Step("align.fit", out_dir=out, params=p, unit=sc) as step:
        d = inputs.assemble(sc, p, step)
        m = method.fit(d, p)
        lines = scope.read(sc, "flightlines", "lines").to_pandas()
        passes = scope.read(sc, "flightlines", "passes").to_pandas()
        members = scope.read(sc, "flightlines", "line_members").to_pandas()
        doc, mem, arrays = method.to_model(m, d, sc, lines, passes, members, params.sha256("align"))
        mp = step.output(io.write_table(mem, out / "members.parquet", "model_members"), "model_members")
        doc["files"]["members.parquet"] = io.sha256_file(mp)
        if arrays is not None:
            import numpy as np

            with io.atomic_path(out / "stage2.npz") as tmp:
                with open(tmp, "wb") as f:
                    np.savez_compressed(f, **arrays)
            doc["files"]["stage2.npz"] = io.sha256_file(step.output(out / "stage2.npz"))
        step.output(io.write_json(doc, out / "model.json", "model"), "model")
    print(
        f"{sc}: {len(d)} obs ({d.attrs['n_screened']} screened out), {len(m.s1.lines)} lines, "
        f"{len(m.s1.spline)} blocks, stage-2 fields for {len(m.s2.lines) if m.s2 else 0} lines; "
        f"weighted RMS {doc['fit']['rms_m'] * 100:.2f} cm"
    )


def register(sub):
    p = sub.add_parser("align", help="fit and apply the correction model").add_subparsers(dest="sub", required=True)
    s = p.add_parser("fit", help="fit the model of a region (or 'statewide')")
    s.add_argument("region")
    s.set_defaults(fn=_fit)
