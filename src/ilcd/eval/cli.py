"""`ilcd eval ...` commands."""

from ..common import io, paths
from ..common.log import get_logger
from ..common.manifest import Step

log = get_logger(__name__)


def _cv(args):
    from ..align import inputs, method, stage1
    from ..align.cli import scope_params
    from . import cv

    sc, params = scope_params(args.region)
    pa_, pe = params.section("align"), params.section("eval.cv")
    out = paths.scope_dir(sc) / "eval"
    with Step("eval.cv", out_dir=out, params={"align": pa_, "eval.cv": pe}, unit=sc) as step:
        d = inputs.assemble(sc, pa_, step)
        ranges = stage1.scan_ranges(d, pa_["stage1"])
        stages = _stages(method, stage1, pa_, ranges)
        res = cv.run(d, stages, pe)
        doc = {
            "scope": sc,
            "method": method.NAME,
            "fold_unit": pe["fold_unit"],
            "segment_unit": pe["segment_unit"],
            "n_folds": pe["folds"],
            "stages": res,
        }
        step.output(io.write_json(doc, out / "cv.json", "cv"), "cv")
    for s in res:
        print(
            f"{sc} {s['name']:12s} segment RMS {s['rms_m'] * 100:.2f} cm  narrow NMAD {s['nmad_m'] * 100:.2f} cm  "
            f"({s['n']} segments)"
        )


def _stages(method, stage1, pa_, ranges) -> dict:
    return {
        "line_offset": lambda tr: method.fit(tr, pa_, terms=stage1.TERMS_M3, ranges=ranges, with_stage2=False),
        "stage1": lambda tr: method.fit(tr, pa_, terms=stage1.TERMS_S1, ranges=ranges, with_stage2=False),
        "stage2": lambda tr: method.fit(tr, pa_, terms=stage1.TERMS_S1, ranges=ranges),
    }


def _injection(args):
    from ..align import inputs, method, stage1
    from ..align.cli import scope_params
    from . import injection

    sc, params = scope_params(args.region)
    pa_, pi = params.section("align"), params.section("eval.injection")
    out = paths.scope_dir(sc) / "eval"
    with Step("eval.injection", out_dir=out, params={"align": pa_, "eval.injection": pi}, unit=sc) as step:
        d = inputs.assemble(sc, pa_, step)
        ranges = stage1.scan_ranges(d, pa_["stage1"])
        stages = {k: v for k, v in _stages(method, stage1, pa_, ranges).items() if k != "line_offset"}
        res = injection.run(d, stages, method.rows(d, pi["target_kinds"]), pi)
        doc = {
            "scope": sc,
            "amount_m": pi["amount_m"],
            "n_centres": pi["n_centres"],
            "seed": pi["seed"],
            "results": res,
        }
        step.output(io.write_json(doc, out / "injection.json", "injection"), "injection")
    for r in res:
        print(
            f"{sc} {r['stage']:7s} R {r['radius_m']:6.0f} m absorbed median {r['absorbed_median']:.3f} "
            f"p90 {r['absorbed_p90']:.3f}"
        )


def register(sub):
    p = sub.add_parser("eval", help="evaluate corrections and change maps").add_subparsers(dest="sub", required=True)
    s = p.add_parser("cv", help="cross-validation of the correction of a region (or 'statewide')")
    s.add_argument("region")
    s.set_defaults(fn=_cv)
    s = p.add_parser("injection", help="synthetic change injection for a region (or 'statewide')")
    s.add_argument("region")
    s.set_defaults(fn=_injection)
