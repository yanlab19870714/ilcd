"""Passes of one legacy file (contract `passes`).

A pass is the points of one file in one continuous stretch of GPS time: sorted by time, a gap longer than
flightlines.gap_s starts a new pass (old swath_analysis.load). Per pass: heading and speed from least-squares x(t) and
y(t) of all its points (old pass_times.py), the midpoint of its x/y extent as centre (used for linking), and two
diagnostics from pass_nadir.py: the ground track from near-nadir returns and the flying height from the
cross-track offset ~ H tan(scan angle).
"""

import numpy as np
import pyarrow as pa

from ..common import contracts, ids, io, paths
from ..common.log import get_logger
from ..common.manifest import Step
from ..data_src import catalog, points

log = get_logger(__name__)


def split(t: np.ndarray, gap_s: float) -> np.ndarray:
    """Pass index (0-based, in time order) of every point."""
    o = np.argsort(t, kind="stable")
    cut = np.r_[0, np.flatnonzero(np.diff(t[o]) > gap_s) + 1, o.size]
    p = np.empty(o.size, np.int32)
    for k, (a, b) in enumerate(zip(cut[:-1], cut[1:], strict=True)):
        p[o[a:b]] = k
    return p


def track(t, x, y, tm):
    """Least-squares x(t), y(t): heading (deg, clockwise from grid north), speed, position at tm, velocity."""
    a = np.c_[t - tm, np.ones(t.size)]
    (vx, x0), (vy, y0) = np.linalg.lstsq(a, x, rcond=None)[0], np.linalg.lstsq(a, y, rcond=None)[0]
    return (
        float((np.degrees(np.arctan2(vx, vy)) + 360) % 360),
        float(np.hypot(vx, vy)),
        (float(x0), float(y0)),
        (vx, vy),
    )


def describe(t, x, y, scan, cls, src, nadir: dict) -> dict:
    """The contract fields of one pass (without ids)."""
    t0, t1 = float(t.min()), float(t.max())
    tm = (t0 + t1) / 2
    heading, speed, c_all, (vx, vy) = track(t, x, y, tm)
    row = {
        "t_start_s": t0,
        "t_end_s": t1,
        "n_points": int(t.size),
        "n_ground": int(np.sum(cls == 2)),
        "heading_deg": heading,
        "speed_mps": speed,
        "cx_m": float((x.min() + x.max()) / 2),
        "cy_m": float((y.min() + y.max()) / 2),
        "xmin_m": float(x.min()),
        "ymin_m": float(y.min()),
        "xmax_m": float(x.max()),
        "ymax_m": float(y.max()),
        "scan_min_deg": float(scan.min()),
        "scan_max_deg": float(scan.max()),
        "nadir_x_m": None,
        "nadir_y_m": None,
        "height_m": None,
    }
    v, cnt = np.unique(src, return_counts=True)
    row["psid_mode"] = int(v[np.argmax(cnt)])
    row["psid_share"] = float(cnt.max() / t.size)
    row["class12_share"] = float(np.mean(cls == 12))
    n = np.abs(scan) <= nadir["max_scan_deg"]
    if n.sum() >= nadir["min_points"] and (t[n].max() - t[n].min()) >= nadir["min_span_share"] * (t1 - t0):
        _, _, (nx, ny), _ = track(t[n], x[n], y[n], tm)
        row["nadir_x_m"], row["nadir_y_m"] = nx, ny
    if speed > 0 and np.ptp(scan) > 1:
        h = np.radians(heading)
        e = (x - c_all[0] - vx * (t - tm)) * np.cos(h) - (y - c_all[1] - vy * (t - tm)) * np.sin(h)
        (k, _), *_ = np.linalg.lstsq(np.c_[np.tan(np.radians(scan)), np.ones(t.size)], e, rcond=None)
        row["height_m"] = float(abs(k))
    return row


def file_passes(file_id: str, params) -> pa.Table:
    d = points.read_legacy_file(file_id, params)
    delivery = catalog.legacy_file(file_id)["delivery_id"]
    domain = catalog.read_deliveries().loc[delivery, "time_domain"]
    if domain is None:
        raise RuntimeError(f"delivery {delivery} has no time domain yet (run data_src headers, then deliveries)")
    rows = []
    if d["t"].size:
        p = split(d["t"], params["flightlines.gap_s"])
        nadir = params.section("flightlines.nadir")
        for k in range(p.max() + 1):
            m = p == k
            row = describe(d["t"][m], d["x"][m], d["y"][m], d["scan_deg"][m], d["cls"][m], d["src"][m], nadir)
            rows.append(
                {
                    "pass_id": ids.pass_id(file_id, k + 1),
                    "file_id": file_id,
                    "delivery_id": delivery,
                    "time_domain": domain,
                    **row,
                }
            )
    return pa.Table.from_pylist(rows, schema=contracts.get("passes").arrow_schema())


def step_params(file_id: str, params) -> dict:
    """What a file's passes depend on besides its points: flightlines params and its delivery's decisions."""
    d = catalog.legacy_file(file_id)["delivery_id"]
    return {**params.section("flightlines"), "delivery": catalog.delivery_facts([d])[d]}


def file_passes_step(file_id: str, params):
    out_dir = paths.file_dir(file_id)
    with Step("flightlines.passes", out_dir=out_dir, params=step_params(file_id, params), unit=file_id) as step:
        t = file_passes(file_id, params)
        step.output(io.write_table(t, out_dir / "passes.parquet", "passes"), "passes")
        if t.num_rows == 0:
            step.empty("no points after cleaning")
    return t
