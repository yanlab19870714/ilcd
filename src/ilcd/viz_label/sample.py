"""Review sample of a site's change objects and their 3-D scene squares (contract `scenes`; design.md section 7.2).

Strata: kind x area bin (viz_label.review.area_bins_m2) x near/far from water (near_water_m; an object without a
water distance is far). Up to per_stratum objects per stratum, drawn with a fixed seed; strata in a fixed order, so
scene ids are stable for the same objects and parameters.
"""

import numpy as np
import pandas as pd
from shapely.geometry import box

from ..common import grid


def strata(obj: pd.DataFrame, p: dict) -> pd.Series:
    edges = [0.0, *p["area_bins_m2"], np.inf]
    names = [f"{a:g}-{b:g}" if np.isfinite(b) else f">={a:g}" for a, b in zip(edges[:-1], edges[1:], strict=True)]
    area = pd.cut(obj["area_m2"].astype(float), edges, right=False, labels=names).astype(str)
    near = np.where(obj["dist_water_m"].astype(float).fillna(np.inf) <= p["near_water_m"], "near", "far")
    return obj["kind"].astype(str) + "/" + area + "/" + near


def square(geom, p: dict):
    """Scene square around an object: min_size_m, or its extent plus 2 x margin_m, centred on its bounds."""
    xmin, ymin, xmax, ymax = geom.bounds
    size = max(p["min_size_m"], max(xmax - xmin, ymax - ymin) + 2 * p["margin_m"])
    size = float(np.ceil(size / 10.0) * 10.0)
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    return box(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2), size


def choose(obj, review: dict, scene: dict) -> pd.DataFrame:
    """obj: GeoDataFrame of contract `objects`. Returns rows of contract `scenes` (with a shapely geometry)."""
    st = strata(obj, review)
    rng = np.random.default_rng(review["seed"])
    rows = []
    for s in sorted(st.unique()):
        idx = np.flatnonzero((st == s).to_numpy())
        pick = np.sort(rng.choice(idx, min(review["per_stratum"], idx.size), replace=False))
        for i in pick:
            g, size = square(obj.geometry.iloc[i], scene)
            rows.append(
                {
                    "scene_id": f"s{len(rows) + 1:03d}",
                    "object_id": obj["object_id"].iloc[i],
                    "stratum": s,
                    "size_m": size,
                    "cells": grid.cells_for_geometry(g),
                    "geometry": g,
                }
            )
    return pd.DataFrame(rows, columns=["scene_id", "object_id", "stratum", "size_m", "cells", "geometry"])
