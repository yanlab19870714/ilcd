import numpy as np
import pandas as pd
import pytest

from ilcd.common import config
from ilcd.flightlines import link, passes


@pytest.fixture(scope="module")
def p():
    return config.default_params().section("flightlines")


def test_split_at_gaps():
    t = np.array([5.0, 0.0, 1.0, 20.0, 21.0, 2.0])
    assert passes.split(t, 5.0).tolist() == [0, 0, 0, 1, 1, 0]


def test_describe_heading_speed_and_height(p):
    rng = np.random.default_rng(0)
    t = np.sort(rng.uniform(0, 20, 5000))
    scan = rng.uniform(-20, 20, t.size)
    h = 2000.0
    # eastbound at 65 m/s; a return at scan angle a lies h tan(a) to the right of the track (n = (cos h, -sin h))
    right = h * np.tan(np.radians(scan))
    x = 500000 + 65 * t
    y = 4300000 - right
    row = passes.describe(t, x, y, scan, np.full(t.size, 2), np.full(t.size, 7), p["nadir"])
    # random scan angles scatter points across track, which tilts the x(t), y(t) fit a little (as in real tiles)
    assert row["heading_deg"] == pytest.approx(90, abs=1.5) and row["speed_mps"] == pytest.approx(65, rel=0.01)
    assert row["height_m"] == pytest.approx(h, rel=0.02)
    assert row["nadir_y_m"] == pytest.approx(4300000, abs=5) and row["psid_mode"] == 7


def _pass(pid, file, t0, cx, cy, heading, speed=65.0, dur=20.0, domain="abs"):
    return dict(
        pass_id=pid,
        file_id=file,
        delivery_id="W_X",
        time_domain=domain,
        t_start_s=t0,
        t_end_s=t0 + dur,
        n_points=1000,
        heading_deg=heading,
        speed_mps=speed,
        cx_m=cx,
        cy_m=cy,
        xmin_m=cx - 700,
        ymin_m=cy - 700,
        xmax_m=cx + 700,
        ymax_m=cy + 700,
        psid_mode=1,
        psid_share=1.0,
        delivery=0,
    )


def test_link_two_lines_and_domains(p):
    rows = []
    # line A eastbound through files f0..f4 (one file per 1500 m), line B westbound 900 s later, 600 m north
    for k in range(5):
        rows.append(_pass(f"W.f{k}#1", f"W.f{k}", 1000 + k * 1500 / 65, 500000 + k * 1500, 4300000, 90.0))
        rows.append(_pass(f"W.f{k}#2", f"W.f{k}", 1900 + (4 - k) * 1500 / 65, 500000 + k * 1500, 4300600, 270.0))
    # a pass in another time domain at the same place and time never joins
    rows.append(_pass("W.g0#1", "W.g0", 1000 + 1500 / 65, 501500, 4300000, 90.0, domain="wk:W_Y"))
    P = pd.DataFrame(rows)
    lab = link.link(P, p)
    a = {lab[i] for i in range(0, 10, 2)}
    b = {lab[i] for i in range(1, 10, 2)}
    assert len(a) == 1 and len(b) == 1 and a != b and lab[10] not in a | b
    lines, members, blocks = link.build(P, p)
    L = lines.to_pandas()
    assert len(L) == 3 and sorted(L["n_passes"]) == [1, 5, 5]
    assert set(blocks["time_domain"]) == {"abs", "wk:W_Y"}


def test_link_rejects_wrong_along_track_position(p):
    rows = [
        _pass("W.f0#1", "W.f0", 1000, 500000, 4300000, 90.0),
        _pass("W.f1#1", "W.f1", 1000 + 1500 / 65, 500000 + 1500 + 2 * p["link"]["pos_tol_m"], 4300000, 90.0),
    ]
    lab = link.link(pd.DataFrame(rows), p)
    assert lab[0] != lab[1]


def test_purity():
    a, b, na, nb = link.purity([0, 0, 1, 1], ["x", "x", "y", "y"])
    assert (a, b, na, nb) == (1.0, 1.0, 2, 2)
    a, b, _, _ = link.purity([0, 0, 0, 0], ["x", "x", "y", "y"])
    assert a == 0.5 and b == 1.0
