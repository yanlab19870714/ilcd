from datetime import UTC, datetime

import numpy as np
import pytest

from ilcd.common import gpstime


def test_brown_file_date():
    # E.ot_in2011_01251470_12_1 starts at adjusted time -15390699 s: 2011-03-19, GPS week 1627 (checked 2026-11-05)
    d = gpstime.adjusted_to_datetime(-15390699)
    assert (d.year, d.month, d.day) == (2011, 3, 19)
    week, sow = gpstime.adjusted_to_week(-15390699)
    assert int(week) == 1627 and 599000 < float(sow) < 600000


def test_round_trip():
    for t in (-15390699.25, 0.0, 12058542.5):
        w, s = gpstime.adjusted_to_week(t)
        assert float(gpstime.week_to_adjusted(w, s)) == pytest.approx(t, abs=1e-6)
    dt = datetime(2012, 4, 1, 12, tzinfo=UTC)
    assert gpstime.adjusted_to_datetime(gpstime.datetime_to_adjusted(dt)) == dt


def test_classify():
    assert gpstime.classify(False, 147078, 147501) == "week_seconds"  # Monroe
    assert gpstime.classify(True, -15390699, -15389255) == "adjusted_standard"  # Brown
    assert gpstime.classify(True, 100.0, 200.0) == "adjusted_standard"  # 2011-09-14: valid in both encodings
    with pytest.raises(ValueError):
        gpstime.classify(False, -5, 10)
    with pytest.raises(ValueError):
        gpstime.classify(False, 0, 700000)
    with pytest.raises(ValueError):
        gpstime.classify(True, -400e6, -399e6)  # 1998


def test_invalid_times_are_counted_and_masked():
    t = np.r_[np.full(1000, 437771.4), [-1.0, -1.0]]
    ttype, n_bad = gpstime.classify_points(False, t, 0.01)
    assert ttype == "week_seconds" and n_bad == 2
    assert gpstime.valid(False, t).sum() == 1000
    with pytest.raises(ValueError):
        gpstime.classify_points(False, np.r_[t, np.full(100, -1.0)], 0.01)


def test_delivery_override_replaces_the_header_bit(monkeypatch):
    from types import SimpleNamespace

    import laspy

    from ilcd.common import config
    from ilcd.data_src import fetch

    las = SimpleNamespace(header=SimpleNamespace(global_encoding=laspy.header.GlobalEncoding()))  # bit clear
    monkeypatch.setattr(
        config,
        "load_delivery_overrides",
        lambda: {"E_Hamilton": {"gps_time_encoding": "adjusted_standard", "evidence": "x"}},
    )
    assert fetch.adjusted_bit(las, "E_Hamilton") is True
    assert fetch.adjusted_bit(las, "W_Monroe") is False
    t = np.full(100, 15.17e6)
    assert gpstime.classify_points(fetch.adjusted_bit(las, "E_Hamilton"), t, 0.01)[0] == "adjusted_standard"
    with pytest.raises(ValueError):
        gpstime.classify_points(fetch.adjusted_bit(las, "W_Monroe"), t, 0.01)
