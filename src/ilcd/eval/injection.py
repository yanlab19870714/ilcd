"""Synthetic change injection (contract `injection`; old fit_field.injection / fit_tradeoff.inject).

n_centres observations of the target kinds are drawn (fixed seed); every target observation within radius R of a
centre gets +amount or -amount (alternating by centre, first come first served). The method is refitted on the
changed observations; the share of the change it absorbs is (prediction with change - prediction without) / amount,
reported as the median and 90th percentile over the changed observations. A good correction absorbs little of
changes smaller than its own spatial scale.
"""

import numpy as np
import pandas as pd


def run(d: pd.DataFrame, stages: dict, target: np.ndarray, p: dict) -> list[dict]:
    """stages: name -> fit(d) returning an object with predict(d). target: boolean mask of candidate rows."""
    xy = d[["x_m", "y_m"]].to_numpy()
    cand = np.flatnonzero(target)
    out = []
    base = {s: fit(d).predict(d) for s, fit in stages.items()}
    rng = np.random.default_rng(p["seed"])
    for radius in p["radii_m"]:
        amount = np.zeros(len(d))
        hit = np.zeros(len(d), bool)
        for i, ci in enumerate(rng.choice(cand, min(p["n_centres"], cand.size), replace=False)):
            m = (np.hypot(*(xy - xy[ci]).T) < radius) & target & ~hit
            amount[m] = p["amount_m"] * (1 if i % 2 else -1)
            hit |= m
        changed = d.assign(dz_m=d["dz_m"].to_numpy() + amount)
        for s, fit in stages.items():
            a = (fit(changed).predict(changed) - base[s])[hit] / amount[hit]
            out.append(
                {
                    "stage": s,
                    "radius_m": float(radius),
                    "absorbed_median": float(np.median(a)),
                    "absorbed_p90": float(np.percentile(a, 90)),
                }
            )
    return out
