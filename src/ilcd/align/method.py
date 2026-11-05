"""The correction-method interface of ilcd's own model (docs/design.md section 8).

fit(d, params) -> Stage1         d from align.inputs.assemble; params = params.section('align')
Stage1.predict(d)                 correction at observation rows
to_model(...)                     the `model` contract document and its members table

Point correction (apply) is added with the change module (refactor step 4).
"""

import numpy as np
import pyarrow as pa

from ..common import contracts
from . import stage1

NAME = "ilcd_two_stage"


def fit(d, params: dict, terms=stage1.TERMS_S1, ranges=None) -> stage1.Stage1:
    return stage1.fit(d, params["stage1"], terms=terms, ranges=ranges)


def to_model(m: stage1.Stage1, d, sc: str, lines, passes, members, params_sha256: str):
    """(model.json document, members table). lines/passes/members: the scope's flightlines tables (pandas)."""
    L = lines.set_index("line_id")
    doc_lines = {}
    for lid, dz0 in m.lines.items():
        r = L.loc[lid]
        doc_lines[lid] = {
            "dz0_m": dz0,
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
    r = d["dz_m"].to_numpy() - m.predict(d)
    doc = {
        "method": NAME,
        "scope": sc,
        "crs": "EPSG:6345+5703",
        "terms": list(m.terms),
        "lines": doc_lines,
        "blocks": blocks,
        "modern": {},
        "median_offset_m": m.median_offset,
        "fit": {
            "n_obs": int(len(d)),
            "n_overlap_obs": 0,
            "rms_m": float(np.sqrt(np.average(r**2, weights=w))),
            "params_sha256": params_sha256,
        },
        "files": {"members.parquet": ""},
    }
    return doc, members_t.cast(contracts.get("model_members").arrow_schema(), safe=True)
