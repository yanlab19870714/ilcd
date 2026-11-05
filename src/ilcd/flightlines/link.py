"""Flight lines and blocks of one scope (contracts `lines`, `line_members`, `blocks`).

Two passes of different files and the same time domain are joined when (old link_lines.py, kept by the nadir test
of 2026-11-04): their headings differ by less than link.max_turn_deg; the later one's mid time is at most
link.max_span_s after the earlier one's; the along-track distance between their centres equals speed x time within
link.pos_tol_m; and their cross-track offset is under link.cross_tol_m. Lines are the connected groups.

Blocks group the lines of a time domain whose time spans are less than block.max_gap_s apart.
"""

from functools import partial

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
from shapely.geometry import MultiPoint

from ..common import contracts, crs, ids
from ..common.log import get_logger

log = get_logger(__name__)


def _unit(heading_deg):
    h = np.radians(np.asarray(heading_deg, float))
    return np.c_[np.sin(h), np.cos(h)]


def _root(parent: np.ndarray, i: int) -> int:
    """Union-find root with path halving."""
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def link(passes: pd.DataFrame, p: dict) -> np.ndarray:
    """Group label (0..) of every row of `passes` (order kept)."""
    lk = p["link"]
    labels = np.full(len(passes), -1, np.int64)
    offset = 0
    for _, g in passes.groupby("time_domain", sort=True):
        g = g.assign(_tm=(g["t_start_s"] + g["t_end_s"]) / 2).sort_values(["_tm", "pass_id"])
        idx = g.index.to_numpy()
        tm, hd, v = g["_tm"].to_numpy(), g["heading_deg"].to_numpy(float), g["speed_mps"].to_numpy(float)
        c = g[["cx_m", "cy_m"]].to_numpy(float)
        u = _unit(hd)
        f = g["file_id"].to_numpy()
        ok = g["n_points"].to_numpy() >= p["min_pass_points"]
        parent = np.arange(len(g))
        root = partial(_root, parent)
        for i in range(len(g)):
            if not ok[i]:
                continue
            for j in range(i + 1, np.searchsorted(tm, tm[i] + lk["max_span_s"], side="right")):
                if not ok[j] or f[j] == f[i] or abs((hd[i] - hd[j] + 180) % 360 - 180) >= lk["max_turn_deg"]:
                    continue
                w = u[i] + u[j]
                w /= np.linalg.norm(w)
                d = c[j] - c[i]
                along, cross = d @ w, abs(d[0] * w[1] - d[1] * w[0])
                flown = (v[i] + v[j]) / 2 * (tm[j] - tm[i])
                if abs(along - flown) < lk["pos_tol_m"] and cross < lk["cross_tol_m"]:
                    parent[root(j)] = root(i)
        roots = np.array([root(i) for i in range(len(g))])
        _, lab = np.unique(roots, return_inverse=True)
        labels[passes.index.get_indexer(idx)] = lab + offset
        offset += lab.max() + 1
    return labels


def build(passes: pd.DataFrame, p: dict) -> tuple[pa.Table, pa.Table, gpd.GeoDataFrame]:
    """(lines, line_members, blocks) of a scope from its passes."""
    passes = passes.reset_index(drop=True)
    lab = link(passes, p)
    members, lines, taken = [], [], {}
    order = {
        g: (passes.loc[lab == g, "t_start_s"].min(), passes.loc[lab == g, "pass_id"].min()) for g in np.unique(lab)
    }
    for g in sorted(order, key=order.get):
        q = passes[lab == g].sort_values("t_start_s")
        domain = q["time_domain"].iloc[0]
        base = ids.line_id(domain, float(q["t_start_s"].iloc[0]))
        taken[base] = taken.get(base, 0) + 1
        lid = ids.line_id(domain, float(q["t_start_s"].iloc[0]), taken[base])
        w = (_unit(q["heading_deg"]) * q["n_points"].to_numpy()[:, None]).sum(0)
        w /= np.linalg.norm(w)
        x0, y0 = float(q["cx_m"].iloc[0]), float(q["cy_m"].iloc[0])
        dx, dy = q["cx_m"].to_numpy() - x0, q["cy_m"].to_numpy() - y0
        along, cross = dx * w[0] + dy * w[1], dx * w[1] - dy * w[0]
        lines.append(
            {
                "line_id": lid,
                "time_domain": domain,
                "t_start_s": float(q["t_start_s"].min()),
                "t_end_s": float(q["t_end_s"].max()),
                "n_passes": len(q),
                "heading_deg": float((np.degrees(np.arctan2(w[0], w[1])) + 360) % 360),
                "x0_m": x0,
                "y0_m": y0,
                "length_m": float(np.ptp(along)),
                "n_points": int(q["n_points"].sum()),
            }
        )
        members += [
            {"pass_id": pid, "line_id": lid, "along_m": float(a), "cross_m": float(c)}
            for pid, a, c in zip(q["pass_id"], along, cross, strict=True)
        ]
    L = pd.DataFrame(lines)
    L["block_id"] = _blocks(L, p["block"]["max_gap_s"])
    blocks = []
    for b, q in L.groupby("block_id"):
        pids = {m["pass_id"] for m in members if m["line_id"] in set(q["line_id"])}
        pp = passes[passes["pass_id"].isin(pids)]
        corners = np.r_[
            pp[["xmin_m", "ymin_m"]].to_numpy(),
            pp[["xmax_m", "ymax_m"]].to_numpy(),
            pp[["xmin_m", "ymax_m"]].to_numpy(),
            pp[["xmax_m", "ymin_m"]].to_numpy(),
        ]
        blocks.append(
            {
                "block_id": b,
                "time_domain": q["time_domain"].iloc[0],
                "t_start_s": float(q["t_start_s"].min()),
                "t_end_s": float(q["t_end_s"].max()),
                "n_lines": len(q),
                "deliveries": sorted(pp["delivery_id"].unique()),
                "geometry": MultiPoint(corners).convex_hull,
            }
        )
    lt = pa.Table.from_pandas(L, preserve_index=False)
    mt = pa.Table.from_pylist(members, schema=contracts.get("line_members").arrow_schema())
    bg = gpd.GeoDataFrame(blocks, geometry="geometry", crs=crs.PROJECT_CRS)
    return lt, mt, bg


def _blocks(lines: pd.DataFrame, max_gap_s: float) -> list[str]:
    """Block id of every line: lines of one time domain, split where consecutive lines are > max_gap_s apart."""
    out = pd.Series(index=lines.index, dtype=object)
    for _, g in lines.groupby("time_domain"):
        g = g.sort_values("t_start_s")
        start, end = None, -np.inf
        for i, r in g.iterrows():
            if start is None or r["t_start_s"] - end > max_gap_s:
                start = ids.block_id(r["time_domain"], r["t_start_s"])
            end = max(end, r["t_end_s"])
            out[i] = start
    return out.tolist()


def purity(groups, truth) -> tuple[float, float, int, int]:
    """Majority purity both ways between two labelings of the same items: (a in b, b in a, n groups a, n groups b)."""
    df = pd.DataFrame({"a": list(groups), "b": list(truth)})

    def pur(x, y):
        return df.groupby(x)[y].agg(lambda s: s.value_counts().iloc[0]).sum() / len(df)

    return float(pur("a", "b")), float(pur("b", "a")), int(df["a"].nunique()), int(df["b"].nunique())
