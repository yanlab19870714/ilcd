"""The site page (design.md section 7.2): templates/site.html with the site's objects embedded; the overview image
and the scene files are loaded by relative URL from the same folder.

Object outlines are simplified to OUTLINE_TOL_M and stored as rings of integers in half metres from the overview's
south-west corner.
"""

import json
from importlib import resources

import numpy as np

OUTLINE_TOL_M = 0.5


def rings(geom, x0: float, y0: float) -> list[list[int]]:
    g = geom.simplify(OUTLINE_TOL_M, preserve_topology=True)
    polys = list(g.geoms) if hasattr(g, "geoms") else [g]
    out = []
    for p in polys:
        for r in [p.exterior, *p.interiors]:
            xy = np.asarray(r.coords)[:-1]
            out.append(np.round(np.c_[xy[:, 0] - x0, xy[:, 1] - y0] * 2).astype(int).ravel().tolist())
    return out


def site_data(site: dict, objects, scenes, overview: dict, params: dict, classes) -> dict:
    """objects: GeoDataFrame `objects`; scenes: DataFrame `scenes`; overview: overview.json."""
    scene_of = dict(zip(scenes["object_id"], scenes["scene_id"], strict=True))
    x0, y0 = overview["xmin"], overview["ymin"]
    rows = []
    for o in objects.itertuples():
        rows.append(
            [
                o.object_id,
                o.kind,
                round(float(o.area_m2)),
                round(float(o.volume_m3), 1),
                round(float(o.mean_dz_m), 2),
                round(float(o.p90_abs_dz_m), 2),
                None if o.dist_water_m is None or np.isnan(o.dist_water_m) else round(float(o.dist_water_m), 1),
                round(float(o.slope_deg), 1),
                scene_of.get(o.object_id),
                rings(o.geometry, x0, y0),
            ]
        )
    return {
        "site": site,
        "overview": overview,
        "fields": [
            "object_id",
            "kind",
            "area_m2",
            "volume_m3",
            "mean_dz_m",
            "p90_abs_dz_m",
            "dist_water_m",
            "slope_deg",
            "scene_id",
            "outline",
        ],
        "objects": rows,
        "scenes": scenes[["scene_id", "object_id", "stratum", "size_m"]].to_dict("records"),
        "classes": list(classes),
        "params": params,
    }


def render(data: dict) -> str:
    tpl = resources.files("ilcd.viz_label").joinpath("templates/site.html").read_text()
    blob = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    if tpl.count("__DATA__") != 1:
        raise ValueError("templates/site.html must contain __DATA__ once")
    return tpl.replace("__DATA__", blob)
