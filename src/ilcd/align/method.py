"""The correction-method interface of ilcd's own model (docs/design.md section 8).

fit(d, params) -> Model           d from align.inputs.assemble; params = params.section('align')
Model.predict(d)                  correction at observation rows (stage 1 + stage 2)
to_model(...)                     the `model` contract document, its members table and stage-2 coefficients

Which observations each stage is fitted on: align.stage1.fit_kinds and align.stage2.fit_kinds (patch kinds, or
"all"). The defaults follow the exploration's release fit (final_fit.py): stage 1 on every stable patch, stage 2 on
road patches. Point correction (apply) is added with the change module (refactor step 4).
"""

from dataclasses import dataclass

import numpy as np
import pyarrow as pa

from ..common import contracts
from . import stage1, stage2

NAME = "ilcd_two_stage"


@dataclass
class Model:
    s1: stage1.Stage1
    s2: stage2.Stage2 | None = None

    def predict(self, d) -> np.ndarray:
        p = self.s1.predict(d)
        return p + self.s2.predict(d) if self.s2 is not None else p


def rows(d, kinds) -> np.ndarray:
    """Rows of the given patch kinds; [all] means every row."""
    return np.ones(len(d), bool) if "all" in kinds else d["kind"].isin(kinds).to_numpy()


def fit(d, params: dict, terms=stage1.TERMS_S1H, ranges=None, with_stage2: bool = True) -> Model:
    m1 = rows(d, params["stage1"]["fit_kinds"])
    if "line_hshift" in terms:  # sloped ground carries the horizontal-shift signal
        m1 |= (d["kind"] == "natural_slope").to_numpy()
    s1 = stage1.fit(d[m1], params["stage1"], terms=terms, ranges=ranges)
    if not with_stage2:
        return Model(s1)
    m2 = rows(d, params["stage2"]["fit_kinds"])
    d2 = d[m2]
    return Model(s1, stage2.fit(d2, d2["dz_m"].to_numpy() - s1.predict(d2), params["stage2"]))


def to_model(model: Model, d, sc: str, lines, passes, members, params_sha256: str):
    """(model.json document, members table, stage-2 arrays for np.savez or None).
    lines/passes/members: the scope's flightlines tables (pandas)."""
    m = model.s1
    L = lines.set_index("line_id")
    doc_lines = {}
    for lid, dz0 in m.lines.items():
        r = L.loc[lid]
        hs = m.hshift.get(lid)
        doc_lines[lid] = {
            "dz0_m": dz0,
            **({"left_m": hs[0], "fwd_m": hs[1], "hshift_pooled": hs[2]} if hs else {}),
            "x0_m": float(r["x0_m"]),
            "y0_m": float(r["y0_m"]),
            "heading_deg": float(r["heading_deg"]),
            "block_id": r["block_id"],
        }
    origin = d.attrs["along_origin_km"]
    blocks = {}
    for b in sorted(set(L.loc[list(m.lines), "block_id"])):
        e = {"along_origin_km": {ax: v for (bb, ax), v in origin.items() if bb == b}}
        if b in m.trend:
            e["trend_m_per_km"] = m.trend[b]
        if b in m.hscan:
            e["hshift_scan_m_per_deg"] = {"left": m.hscan[b][0], "forward": m.hscan[b][1]}
        if b in m.spline:
            lo, hi, k, coef = m.spline[b]
            e["scan_spline"] = {
                "knots_deg": np.linspace(lo, hi, k).tolist(),
                "coef_m": [float(c) for c in coef],
                "range_deg": [lo, hi],
            }
        blocks[b] = e
    from .inputs import axis_of

    mem = passes[["pass_id", "file_id", "t_start_s", "t_end_s", "heading_deg"]].merge(
        members[["pass_id", "line_id"]], on="pass_id"
    )
    mem = mem[mem["line_id"].isin(m.lines)]
    mem["axis"] = axis_of(mem["heading_deg"])
    members_t = pa.Table.from_pandas(mem.drop(columns="heading_deg"), preserve_index=False)
    w = d["w"].to_numpy()
    r = d["dz_m"].to_numpy() - model.predict(d)
    doc = {
        "method": NAME,
        "scope": sc,
        "crs": "EPSG:6345+5703",
        "terms": list(m.terms) + (["line_field"] if model.s2 is not None else []),
        "lines": doc_lines,
        "blocks": blocks,
        "modern": {b: {"dz_m": v, "fixed": b == m.meta.get("modern_ref")} for b, v in m.modern.items()},
        "median_offset_m": m.median_offset,
        "fit": {
            "n_obs": int(len(d)),
            "n_overlap_obs": 0,
            "rms_m": float(np.sqrt(np.average(r**2, weights=w))),
            "params_sha256": params_sha256,
        },
        "files": {"members.parquet": ""},
    }
    arrays = None
    if model.s2 is not None:
        doc["stage2"] = {"spacing_m": model.s2.spacing_m, "lam": model.s2.lam}
        arrays = {}
        for lid, (tx, ty, c) in model.s2.lines.items():
            arrays[f"{lid}|tx"], arrays[f"{lid}|ty"], arrays[f"{lid}|coef"] = tx, ty, c
        doc["files"]["stage2.npz"] = ""
    return doc, members_t.cast(contracts.get("model_members").arrow_schema(), safe=True), arrays
