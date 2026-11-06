"""GPS time conversions (docs/design.md section 5).

LAS files store GPS time either as seconds of the GPS week (global-encoding bit 0 clear) or as adjusted standard GPS
time, GPS seconds minus 1e9 (bit set). ilcd works in adjusted standard time wherever the week is known.

Dates are on the GPS time scale, which runs ahead of UTC by the leap seconds since 1980 (15 s in 2011, 18 s since
2017); that is irrelevant for grouping flights by day.
"""

from datetime import UTC, datetime, timedelta

import numpy as np

WEEK_S = 604800
ADJUSTED_OFFSET_S = 1e9
GPS_EPOCH = datetime(1980, 1, 6, tzinfo=UTC)

# Plausible acquisition window of the 2011-13 legacy data and of 3DEP 2016-20, as adjusted standard time. A file whose
# header says adjusted standard but whose times fall outside it is mislabelled.
ACQUISITION_WINDOW = (datetime(2009, 1, 1, tzinfo=UTC), datetime(2021, 12, 31, tzinfo=UTC))


def datetime_to_adjusted(dt: datetime) -> float:
    return (dt - GPS_EPOCH).total_seconds() - ADJUSTED_OFFSET_S


def adjusted_to_datetime(t: float) -> datetime:
    return GPS_EPOCH + timedelta(seconds=float(t) + ADJUSTED_OFFSET_S)


def adjusted_to_week(t):
    """Adjusted standard time -> (GPS week, seconds of week); scalars or arrays."""
    g = np.asarray(t, dtype=float) + ADJUSTED_OFFSET_S
    week = np.floor(g / WEEK_S)
    return week.astype(np.int64), g - week * WEEK_S


def week_to_adjusted(week, sow):
    """(GPS week, seconds of week) -> adjusted standard time; scalars or arrays."""
    return np.asarray(week, dtype=float) * WEEK_S + np.asarray(sow, dtype=float) - ADJUSTED_OFFSET_S


def classify(adjusted_bit: bool, t_min: float, t_max: float) -> str:
    """Time type of a file from its global-encoding bit, checked against its time range.

    Returns `adjusted_standard` or `week_seconds`; raises ValueError when the range contradicts the bit. Values in
    [0, 604800) are valid in both encodings (adjusted standard 0 is 2011-09-14), so the bit decides and the range only
    rules out impossible values.
    """
    if not (t_min <= t_max):
        raise ValueError(f"bad GPS time range {t_min}..{t_max}")
    if adjusted_bit:
        lo, hi = (datetime_to_adjusted(d) for d in ACQUISITION_WINDOW)
        if t_min < lo or t_max > hi:
            raise ValueError(
                f"header says adjusted standard time but {t_min:.0f}..{t_max:.0f} falls outside "
                f"{ACQUISITION_WINDOW[0]:%Y-%m-%d}..{ACQUISITION_WINDOW[1]:%Y-%m-%d}"
            )
        return "adjusted_standard"
    if t_min < 0 or t_max >= WEEK_S:
        raise ValueError(f"header says GPS week time but {t_min:.0f}..{t_max:.0f} is not within one week")
    return "week_seconds"


def valid(adjusted_bit: bool, t) -> np.ndarray:
    """Points whose GPS time is possible in the file's encoding. Some deliveries hold synthetic points without a
    time (W_Morgan: class 16 at t = -1); they cannot be placed in a pass and are dropped by the readers."""
    t = np.asarray(t, float)
    if adjusted_bit:
        lo, hi = (datetime_to_adjusted(d) for d in ACQUISITION_WINDOW)
        return (t >= lo) & (t <= hi)
    return (t >= 0) & (t < WEEK_S)


def classify_points(adjusted_bit: bool, t, max_invalid_share: float) -> tuple[str, int]:
    """(time type, number of points with an impossible time). More than max_invalid_share of such points means the
    header bit is wrong or the file is damaged: an error."""
    ok = valid(adjusted_bit, t)
    n_bad = int(ok.size - ok.sum())
    if ok.size == 0 or n_bad > max_invalid_share * ok.size:
        raise ValueError(f"{n_bad} of {ok.size} points have a GPS time impossible for the header's encoding")
    t = np.asarray(t, float)[ok]
    return classify(adjusted_bit, float(t.min()), float(t.max())), n_bad
