"""The correction-method interface of ilcd's own model (docs/design.md section 8).

fit(d, params) -> Model           d from align.inputs.assemble; params = params.section('align')
Model.predict(d)                  correction at observation rows (stage 1 + stage 2)
to_model(...)                     the `model` contract document, its members table and stage-2 coefficients
load(model_dir) -> Saved          a model directory written by `ilcd align fit`
apply(saved, pts) -> pts          2011 points corrected into the frame of the model's reference 3DEP batch
to_reference_batch(saved, z, b)   3DEP heights of batch b in that same frame

Which observations each stage is fitted on: align.stage1.fit_kinds and align.stage2.fit_kinds (patch kinds, or
"all"). The defaults follow the exploration's release fit (final_fit.py): stage 1 on every stable patch, stage 2 on
road patches.

Point correction. An observation is 2011 - 3DEP(batch b) = c + m_b, with c the 2011 error and m_b the offset of
batch b (0 for the reference batch). apply() removes c from 2011 points: the vertical terms are subtracted, and the
horizontal shift moves x, y back (a 2011 point sits at its true position + s, stage1). to_reference_batch() adds
m_b to 3DEP heights of batch b, so both epochs end in the reference batch's frame.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyarrow as pa

from ..common import contracts, io
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
    return np.ones(len(d), bool) if "all" in kinds else d["kind"].isin(kinds).to_numpy(copy=True)


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
    if m.slope_off:
        doc["fit"]["sloped_ground_offset"] = {"unit": m.meta["slope_offset"], "offset_m": m.slope_off}
    arrays = None
    if model.s2 is not None:
        doc["stage2"] = {"spacing_m": model.s2.spacing_m, "lam": model.s2.lam}
        arrays = {}
        for lid, (tx, ty, c) in model.s2.lines.items():
            arrays[f"{lid}|tx"], arrays[f"{lid}|ty"], arrays[f"{lid}|coef"] = tx, ty, c
        doc["files"]["stage2.npz"] = ""
    return doc, members_t.cast(contracts.get("model_members").arrow_schema(), safe=True), arrays


@dataclass
class Saved:
    """A model read back from its directory: the fitted terms plus what apply() needs to place points."""

    model: Model
    doc: dict
    members: pd.DataFrame  # pass_id, file_id, t_start_s, t_end_s, line_id, axis
    heading: dict  # line_id -> heading_deg
    block: dict  # line_id -> block_id
    origin: dict  # (block_id, axis) -> along-track origin, km


def load(model_dir) -> Saved:
    doc = io.read_json(model_dir / "model.json", "model")
    members = io.read_table(model_dir / "members.parquet", "model_members").to_pandas()
    blocks = doc["blocks"]
    spline = {}
    for b, e in blocks.items():
        if "scan_spline" in e:
            sp = e["scan_spline"]
            lo, hi = sp["range_deg"]
            spline[b] = (lo, hi, len(sp["knots_deg"]), np.asarray(sp["coef_m"], float))
    s1 = stage1.Stage1(
        terms=tuple(t for t in doc["terms"] if t != "line_field"),
        lines={lid: e["dz0_m"] for lid, e in doc["lines"].items()},
        trend={b: e["trend_m_per_km"] for b, e in blocks.items() if "trend_m_per_km" in e},
        spline=spline,
        median_offset=doc["median_offset_m"],
        modern={b: e["dz_m"] for b, e in doc["modern"].items()},
        hshift={
            lid: (e["left_m"], e["fwd_m"], e.get("hshift_pooled", False))
            for lid, e in doc["lines"].items()
            if "left_m" in e
        },
        hscan={
            b: (e["hshift_scan_m_per_deg"]["left"], e["hshift_scan_m_per_deg"]["forward"])
            for b, e in blocks.items()
            if "hshift_scan_m_per_deg" in e
        },
        slope_off=dict(so["offset_m"]) if (so := doc["fit"].get("sloped_ground_offset")) else {},
        meta={"slope_offset": so["unit"]} if so else {},
    )
    s2 = None
    if "stage2" in doc:
        z = np.load(model_dir / "stage2.npz")
        lines = {k.rsplit("|", 1)[0] for k in z.files}
        s2 = stage2.Stage2(
            spacing_m=doc["stage2"]["spacing_m"],
            lam=doc["stage2"]["lam"],
            lines={lid: (z[f"{lid}|tx"], z[f"{lid}|ty"], z[f"{lid}|coef"]) for lid in lines},
        )
    return Saved(
        model=Model(s1, s2),
        doc=doc,
        members=members,
        heading={lid: e["heading_deg"] for lid, e in doc["lines"].items()},
        block={lid: e["block_id"] for lid, e in doc["lines"].items()},
        origin={(b, ax): v for b, e in blocks.items() for ax, v in e["along_origin_km"].items()},
    )


def _pass_of(saved: Saved, file_ids: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Row of saved.members for each point (-1: no pass of the model covers the point's file and time)."""
    out = np.full(t.size, -1, np.int64)
    mem = saved.members.reset_index(drop=True)
    for f, g in mem.groupby("file_id"):
        m = np.flatnonzero(file_ids == f)
        if not m.size:
            continue
        g = g.sort_values("t_start_s")
        k = np.searchsorted(g["t_start_s"].to_numpy(), t[m], side="right") - 1
        ok = k >= 0
        ok[ok] &= t[m][ok] <= g["t_end_s"].to_numpy()[k[ok]]
        out[m[ok]] = g.index.to_numpy()[k[ok]]
    return out


def apply(saved: Saved, pts: dict, chunk: int = 200_000) -> dict:
    """2011 points (data_src.points: x, y, z, t, scan_deg, part, parts) corrected; adds line_id and `corrected`
    (False where no pass of the model covers the point: it keeps its heights and gets the model's median offset)."""
    n = pts["x"].size
    files = np.asarray(pts["parts"], dtype=object)[pts["part"]] if n else np.empty(0, object)
    row = _pass_of(saved, files, pts["t"])
    mem = saved.members
    line = pd.Series(np.where(row >= 0, mem["line_id"].to_numpy(dtype=object)[np.maximum(row, 0)], None))
    axis = np.where(row >= 0, mem["axis"].to_numpy(dtype=object)[np.maximum(row, 0)], "ns")
    block = line.map(saved.block)
    s1 = saved.model.s1
    x, y, z = pts["x"].astype(float), pts["y"].astype(float), pts["z"].astype(float)
    # horizontal: true = 2011 - s (stage1 module docstring)
    h = np.radians(line.map(saved.heading).fillna(0.0).to_numpy(float))
    scan = pts["scan_deg"].astype(float)

    def per(series, table, k):
        return series.map({key: v[k] for key, v in table.items()}).fillna(0.0).to_numpy(float)

    left = per(line, s1.hshift, 0) + per(block, s1.hscan, 0) * scan
    fwd = per(line, s1.hshift, 1) + per(block, s1.hscan, 1) * scan
    x_true = x - (fwd * np.sin(h) - left * np.cos(h))
    y_true = y - (fwd * np.cos(h) + left * np.sin(h))
    # vertical: stage 1 without the batch offset and without the gradient form of the shift, plus stage 2
    along_raw = np.where(axis == "ns", y_true, x_true) / 1000.0
    key = block.fillna("").astype(str) + "|" + pd.Series(axis, dtype=object)
    origin = key.map({f"{b}|{a}": v for (b, a), v in saved.origin.items()}).fillna(0.0).to_numpy(float)
    d = pd.DataFrame(
        {
            "line_id": line.to_numpy(dtype=object),
            "block_id": block.to_numpy(dtype=object),
            "scan_deg": scan,
            "along_km": along_raw - origin,
            "x_m": x_true,
            "y_m": y_true,
        }
    )
    vert = stage1.Stage1(**{**s1.__dict__, "hshift": {}, "modern": {}})
    c = np.empty(n)
    for i in range(0, n, chunk):
        part = d.iloc[i : i + chunk]
        c[i : i + chunk] = vert.predict(part)
        if saved.model.s2 is not None:
            c[i : i + chunk] += saved.model.s2.predict(part)
    out = dict(pts)
    out.update(x=x_true, y=y_true, z=z - c, line_id=line.to_numpy(dtype=object), corrected=row >= 0)
    return out


def to_reference_batch(saved: Saved, z: np.ndarray, batch: str) -> np.ndarray:
    """3DEP heights of `batch` in the frame of the model's reference batch (adds the fitted batch offset m_b)."""
    return np.asarray(z, float) + saved.model.s1.modern.get(batch, 0.0)
