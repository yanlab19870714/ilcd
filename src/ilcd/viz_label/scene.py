"""Data of one 3-D review scene (old m3c2_view_data.py and dc_view_data.py; design.md section 7.2).

Coordinates are packed as int16 in units of 5 cm: x, y from the square's south-west corner, z from z0 (the lowest
ground point of either epoch). Per scene:
  leg, mod      sampled 2011 (corrected) and 3DEP points (3DEP in the model's reference batch frame), with a
                ground flag (class 2) each
  core          sampled M3C2 core points with distance (int16 cm), level of detection (uint8 cm) and object
                (uint16: 0 = none, k = objects[k - 1])
  t_leg, t_mod  ground-and-water TIN of each epoch on a terrain_res_m grid, rows south to north, -32768 = no return
                within terrain_gap_m
  img_leg, img_mod  orthophotos (JPEG, base64) and their descriptions
"""

import base64
import re

import numpy as np
import pandas as pd

Q = 20.0  # packed units per metre
NA = -32768
TERRAIN_CLASSES = (2, 9)  # water too: ground alone leaves the river a hole


def b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def pack(x, y, z, x0, y0, z0) -> str:
    a = np.stack([(x - x0) * Q, (y - y0) * Q, (z - z0) * Q], 1).round().clip(-32767, 32767).astype("<i2")
    return b64(a)


def sample(n: int, k: int, rng) -> np.ndarray:
    return np.sort(rng.choice(n, k, replace=False)) if n > k else np.arange(n)


def terrain(x, y, z, x0, y0, z0, size, res, gap) -> dict:
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import cKDTree

    n = int(np.ceil(size / res))
    c = (np.arange(n) + 0.5) * size / n
    gx, gy = np.meshgrid(c, c)
    q = np.stack([gx.ravel(), gy.ravel()], 1)
    if x.size < 3:
        return {"n": n, "z": b64(np.full(n * n, NA, "<i2"))}
    xy = np.stack([x - x0, y - y0], 1)
    v = LinearNDInterpolator(xy, z)(q)
    v[cKDTree(xy).query(q, k=1)[0] > gap] = np.nan
    a = np.where(np.isnan(v), NA, np.round((v - z0) * Q).clip(-32767, 32767)).astype("<i2")
    return {"n": n, "z": b64(a)}


def batch_year(batch: str) -> int:
    """Acquisition year in a 3DEP batch name (IN_Statewide_Opt2_B4_2017 -> 2017)."""
    m = re.findall(r"(?<!\d)(20\d\d)(?!\d)", batch)
    if not m:
        raise ValueError(f"no year in 3DEP batch name {batch!r}")
    return int(m[-1])


def naip_year(year: int, years: list[int]) -> int:
    return min(years, key=lambda y: (abs(y - year), -y))


def inside(d: dict, b) -> np.ndarray:
    return (d["x"] >= b[0]) & (d["x"] < b[2]) & (d["y"] >= b[1]) & (d["y"] < b[3])


def build(row, leg: dict, mod: dict, cores: pd.DataFrame, objects: pd.DataFrame, images: dict, p: dict) -> dict:
    """row: one `scenes` row; leg, mod: points of the square (x, y, z, cls; leg already corrected and only
    corrected points); cores: m3c2 rows in the square with an object_id column (None = no object); objects: the
    `objects` rows touching the square (no geometry); images: {"leg"|"mod": (jpeg bytes, description)};
    p = params.section('viz_label.scene')."""
    rng = np.random.default_rng(0)
    x0, y0, x1, _ = row.geometry.bounds
    size = x1 - x0
    g = np.concatenate([leg["z"][leg["cls"] == 2], mod["z"][mod["cls"] == 2]])
    if g.size == 0:
        g = np.concatenate([leg["z"], mod["z"]])
    z0 = float(g.min())
    ztop = float(np.percentile(np.concatenate([leg["z"], mod["z"]]), 99.9))
    out = {
        "scene_id": row.scene_id,
        "object_id": row.object_id,
        "stratum": row.stratum,
        "size_m": size,
        "x0": x0,
        "y0": y0,
        "z0": round(z0, 2),
        "zrange": [0, round(ztop - z0, 2)],
    }
    for k, d in (("leg", leg), ("mod", mod)):
        i = sample(d["x"].size, p["points"], rng)
        out[k] = pack(d["x"][i], d["y"][i], d["z"][i], x0, y0, z0)
        out["g_" + k] = b64((d["cls"][i] == 2).astype(np.uint8))
        t = np.isin(d["cls"], TERRAIN_CLASSES)
        out["t_" + k] = terrain(
            d["x"][t], d["y"][t], d["z"][t], x0, y0, z0, size, p["terrain_res_m"], p["terrain_gap_m"]
        )
        jpg, desc = images[k]
        out["img_" + k] = base64.b64encode(jpg).decode()
        out["imgdesc_" + k] = desc
    ids = list(objects["object_id"])
    index = {o: n + 1 for n, o in enumerate(ids)}
    i = sample(len(cores), p["points"], rng)
    c = cores.iloc[i]
    d = c["d_m"].to_numpy(float)
    out["core"] = pack(c["x_m"].to_numpy(), c["y_m"].to_numpy(), c["z_m"].to_numpy(), x0, y0, z0)
    out["core_d"] = b64(np.nan_to_num(np.round(d * 100), nan=0).clip(-32767, 32767).astype("<i2"))
    out["core_lod"] = b64(np.nan_to_num(np.round(c["lod_m"].to_numpy(float) * 100), nan=255).clip(0, 255).astype("u1"))
    out["core_ok"] = b64(np.isfinite(d).astype("u1"))
    out["core_obj"] = b64(c["object_id"].map(index).fillna(0).to_numpy().astype("<u2"))
    out["objects"] = ids
    return out
