import numpy as np
import pandas as pd
import pytest

from ilcd.align import stage1
from ilcd.align.inputs import axis_of
from ilcd.common import config
from ilcd.eval import cv
from ilcd.refs import screen


@pytest.fixture(scope="module")
def params():
    return config.default_params()


def _synthetic(rng, n=6000):
    """Observations of 6 lines in 2 blocks with known offsets, trends and scan curves."""
    lines = [f"abs@{i}.0" for i in range(6)]
    block = {lid: ("abs@0#B" if i < 3 else "abs@9#B") for i, lid in enumerate(lines)}
    dz0 = dict(zip(lines, [0.10, 0.05, 0.12, 0.07, 0.03, 0.09], strict=True))
    trend = {"abs@0#B": 0.002, "abs@9#B": -0.001}
    curve = {"abs@0#B": lambda s: 0.002 * s, "abs@9#B": lambda s: 0.03 * np.sin(s / 6)}
    li = rng.integers(0, 6, n)
    d = pd.DataFrame(
        {
            "line_id": np.array(lines)[li],
            "scan_deg": rng.uniform(-20, 20, n),
            "along_km": rng.uniform(-10, 10, n),
            "cell": [f"E{500 + c:04d}N4300" for c in rng.integers(0, 40, n)],
            "pass_id": [f"W.f#{k}" for k in li],
            "narrow": rng.random(n) < 0.7,
            "sigma_m": np.full(n, 0.01),
        }
    )
    d["patch_id"] = [f"{c}.R{k:05d}" for c, k in zip(d["cell"], rng.integers(0, 50, n), strict=True)]
    d["block_id"] = d["line_id"].map(block)
    d["dz_m"] = (
        d["line_id"].map(dz0)
        + d["block_id"].map(trend) * d["along_km"]
        + [curve[b](s) for b, s in zip(d["block_id"], d["scan_deg"], strict=True)]
        + rng.normal(0, 0.01, n)
    )
    d["w"] = 1 / (d["sigma_m"] ** 2 + 0.015**2)
    return d, dz0, trend


def test_stage1_recovers_offsets_and_trends(params):
    d, dz0, trend = _synthetic(np.random.default_rng(0))
    m = stage1.fit(d, params["align.stage1"], terms=stage1.TERMS_S1)
    for b, t in trend.items():
        assert m.trend[b] == pytest.approx(t, abs=2e-4)
    # offsets and spline share a constant per block: compare line differences within a block
    assert m.lines["abs@1.0"] - m.lines["abs@0.0"] == pytest.approx(dz0["abs@1.0"] - dz0["abs@0.0"], abs=2e-3)
    r = d["dz_m"] - m.predict(d)
    assert np.std(r) < 0.012
    m3 = stage1.fit(d, params["align.stage1"], terms=stage1.TERMS_M3)
    assert np.std(d["dz_m"] - m3.predict(d)) > np.std(r)  # the scan curve matters in block 2


def test_modern_batch_offset(params):
    d, *_ = _synthetic(np.random.default_rng(4))
    d["modern_batch"] = np.where(np.arange(len(d)) % 4 == 0, "B2", "B1")  # B1 has most observations: reference
    d.loc[d["modern_batch"] == "B2", "dz_m"] -= 0.05  # 2011 minus 3DEP: batch B2 sits 5 cm higher
    m = stage1.fit(d, params["align.stage1"], terms=stage1.TERMS_S1)
    assert m.modern["B1"] == 0.0 and m.modern["B2"] == pytest.approx(-0.05, abs=0.003)
    assert m.meta["modern_ref"] == "B1"
    one = stage1.fit(d.assign(modern_batch="B1"), params["align.stage1"], terms=stage1.TERMS_S1)
    assert one.modern == {}


def test_horizontal_shift_recovered_and_pooled(params):
    rng = np.random.default_rng(5)
    n = 8000
    lines = np.array(["abs@0.0", "abs@1.0", "abs@2.0"])
    li = rng.choice(3, n, p=[0.45, 0.45, 0.10])
    d = pd.DataFrame(
        {
            "line_id": lines[li],
            "block_id": "abs@0#B",
            "along_km": 0.0,
            "scan_deg": rng.uniform(-15, 15, n),
            "heading_deg": np.where(li == 1, 270.0, 90.0),
            "kind": "natural_slope",
            "gx": rng.normal(0, 0.3, n),
            "gy": rng.normal(0, 0.3, n),
            "w": 1.0,
        }
    )
    d.loc[: n // 2, "kind"] = "hard_other"
    d.loc[: n // 2, ["gx", "gy"]] = 0.0
    true = {"abs@0.0": (0.5, -0.3), "abs@1.0": (-0.2, 0.4), "abs@2.0": (0.0, 0.0)}
    cl, cf = stage1.hshift_columns(d)
    left = d["line_id"].map(lambda x: true[x][0]).to_numpy()
    fwd = d["line_id"].map(lambda x: true[x][1]).to_numpy()
    d["dz_m"] = 0.1 + cl * left + cf * fwd + rng.normal(0, 0.01, n)
    p = dict(params["align.stage1"], hshift_min_obs=500)  # line abs@2.0 has ~400 sloped obs: pooled
    m = stage1.fit(d, p, terms=("line_offset", "line_hshift"))
    for lid in ("abs@0.0", "abs@1.0"):
        assert m.hshift[lid][0] == pytest.approx(true[lid][0], abs=0.02)
        assert m.hshift[lid][1] == pytest.approx(true[lid][1], abs=0.02)
        assert m.hshift[lid][2] is False
    assert m.hshift["abs@2.0"][2] is True
    assert np.std(d["dz_m"] - m.predict(d)) < 0.012


def test_unseen_line_uses_median_offset(params):
    d, *_ = _synthetic(np.random.default_rng(1))
    m = stage1.fit(d[d["line_id"] != "abs@5.0"], params["align.stage1"], terms=stage1.TERMS_M3)
    t = d[d["line_id"] == "abs@5.0"].head(3)
    expect = m.median_offset + t["block_id"].map(m.trend).to_numpy() * t["along_km"].to_numpy()
    assert np.allclose(m.predict(t), expect)


def test_cv_runs_and_orders_stages(params):
    d, *_ = _synthetic(np.random.default_rng(2))
    p = dict(params["eval.cv"], repeats=2)
    ranges = stage1.scan_ranges(d, params["align.stage1"])
    stages = {
        "line_offset": lambda tr: stage1.fit(tr, params["align.stage1"], stage1.TERMS_M3, ranges),
        "stage1": lambda tr: stage1.fit(tr, params["align.stage1"], stage1.TERMS_S1, ranges),
    }
    res = {s["name"]: s for s in cv.run(d, stages, p)}
    assert res["stage1"]["rms_m"] < res["line_offset"]["rms_m"] and res["stage1"]["n"] > 0


def test_axis_of():
    assert axis_of([0, 44, 46, 180, 269, 316]).tolist() == ["ns", "ns", "ew", "ns", "ew", "ns"]


def test_screen_flags_outlier_and_gross_cell(params):
    p = params["refs.screen"]
    rng = np.random.default_rng(3)
    n = 60
    patches = pd.DataFrame(
        {
            "patch_id": [f"E0500N4300.R{i:05d}" for i in range(n)],
            "x": rng.uniform(0, 150, n),
            "y": rng.uniform(0, 150, n),
            "legacy_dz_m": rng.normal(0.08, 0.01, n),
            "legacy_se_m": np.full(n, 0.005),
            "n_legacy": np.full(n, 100),
        }
    )
    patches.loc[7, "legacy_dz_m"] = 0.60  # a quarry floor
    cs = screen.change_suspects(patches, p)
    assert cs["patch_id"].tolist() == ["E0500N4300.R00007"]
    obs = pd.DataFrame(
        {"patch_id": [f"E{500 + c:04d}N4300.R00001" for c in range(10)] * 5, "dz_m": [0.08] * 45 + [0.9] * 5}
    )
    obs.loc[45:, "patch_id"] = "E0599N4300.R00001"
    gc = screen.gross_cells(obs, set(), p)
    assert set(gc["patch_id"]) == {"E0599N4300.R00001"}
