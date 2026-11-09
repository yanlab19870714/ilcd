"""`ilcd viz_label ...` commands (design.md section 7.2)."""

import json

from ..common import config, ids, io, paths
from ..common.log import get_logger
from ..common.manifest import Step
from ..data_src.cli import _ids, _todo

log = get_logger(__name__)


def _site_params(site: str):
    sc = config.site_scope(site)
    region = ids.parse_scope(sc)
    return sc, config.default_params() if region is None else config.load_region(region).params


def _objects_path(site):
    return paths.site_dir(site) / "change" / "objects.parquet"


def _scenes_path(site):
    return paths.site_dir(site) / "viz" / "scenes.parquet"


def _scenes(args):
    import geopandas as gpd

    from ..common.crs import PROJECT_CRS
    from . import sample

    _, params = _site_params(args.site)
    p = {"review": params.section("viz_label.review"), "scene": params.section("viz_label.scene")}
    out = paths.site_dir(args.site) / "viz"
    with Step("viz_label.scenes", out_dir=out, params=p, unit=args.site) as step:
        step.input(_objects_path(args.site), "objects")
        obj = io.read_vector(_objects_path(args.site), "objects")
        within = config.site_geometry(args.site).bounds
        s = gpd.GeoDataFrame(sample.choose(obj, p["review"], p["scene"], within), geometry="geometry", crs=PROJECT_CRS)
        step.output(io.write_table(s, _scenes_path(args.site), "scenes"), "scenes")
    print(f"{args.site}: {len(s)} scenes from {len(obj)} objects")
    for k, n in s["stratum"].value_counts().sort_index().items():
        print(f"  {k}: {n}")


def _scene_ids(args):
    """Scene ids of a site (one per line), for --from-file."""
    for s in io.read_table(_scenes_path(args.site), "scenes", columns=["scene_id"]).column("scene_id").to_pylist():
        print(s)


def _read_square(cell, bounds, saved, params, pad):
    """Corrected 2011 points and reference-frame 3DEP points of one cell inside a square (cell interiors only, so
    neighbouring cells do not repeat points), plus the 3DEP batch."""
    import numpy as np

    from ..align import method
    from ..common import grid
    from ..data_src import points
    from .scene import inside

    cb = grid.cell_bounds(cell)
    b = (max(bounds[0], cb[0]), max(bounds[1], cb[1]), min(bounds[2], cb[2]), min(bounds[3], cb[3]))
    leg = method.apply(saved, points.read_legacy(cell, params, pad=pad))
    k = inside(leg, b) & leg["corrected"]
    leg = {f: leg[f][k] for f in ("x", "y", "z", "cls")}
    mod = points.read_modern(cell, params, pad=0)
    k = inside(mod, b)
    batch = mod["parts"][0] if mod["parts"] else None
    mod = {f: mod[f][k] for f in ("x", "y", "z", "cls")}
    if batch is not None:
        mod["z"] = method.to_reference_batch(saved, mod["z"], batch)
    return leg, mod, batch, int(np.sum(k))


def _scene_data(args):
    import numpy as np
    import pandas as pd

    from ..align import method
    from ..data_src import ancillary
    from . import scene

    sc, params = _site_params(args.site)
    p, pm = params.section("viz_label.scene"), params.section("change.m3c2")
    years = params["viz_label.imagery.naip_years"]
    scenes = io.read_vector(_scenes_path(args.site), "scenes").set_index("scene_id", drop=False)
    obj = io.read_vector(_objects_path(args.site), "objects")
    change = paths.site_dir(args.site) / "change"
    members = io.read_table(change / "object_members.parquet", "object_members").to_pandas()
    saved = method.load(paths.model_dir(sc))
    base = paths.site_dir(args.site) / "viz" / "scenes"
    sp = {"scope": sc, **p, "naip_years": years}
    for sid in _todo(_ids(args), args, lambda s: base / s, "viz_label.scene", sp):
        row = scenes.loc[sid]
        bounds = row.geometry.bounds
        out = base / sid
        with Step("viz_label.scene", out_dir=out, params=sp, unit=sid) as step:
            step.input(_scenes_path(args.site), "scenes")
            step.input(paths.model_dir(sc) / "model.json", "model")
            legs, mods, cores, nb = [], [], [], {}
            for cell in row.cells:
                leg, mod, batch, n = _read_square(cell, bounds, saved, params, pm["pad_m"])
                legs.append(leg)
                mods.append(mod)
                if batch is not None:
                    nb[batch] = nb.get(batch, 0) + n
                f = change / "m3c2" / cell / "m3c2.parquet"
                step.input(f, "m3c2")
                t = io.read_table(f, "m3c2", columns=["core_id", "x_m", "y_m", "z_m", "d_m", "lod_m"]).to_pandas()
                t = t[scene.inside({"x": t["x_m"].to_numpy(), "y": t["y_m"].to_numpy()}, bounds)]
                m = members[members["cell_id"] == cell].set_index("core_id")["object_id"]
                cores.append(t.assign(object_id=t["core_id"].map(m)))
            leg = {f: np.concatenate([d[f] for d in legs]) for f in ("x", "y", "z", "cls")}
            mod = {f: np.concatenate([d[f] for d in mods]) for f in ("x", "y", "z", "cls")}
            cores = pd.concat(cores, ignore_index=True)
            batch = max(nb, key=nb.get)
            year = scene.naip_year(scene.batch_year(batch), years)
            key = f"{bounds[0]:.0f}_{bounds[1]:.0f}_{row.size_m:.0f}_{p['image_px']}"
            images = {}
            for k, source in (("leg", "in2011"), ("mod", f"naip{year}")):
                path, desc = ancillary.ortho(bounds, source, p["image_px"], key)
                images[k] = (path.read_bytes(), desc)
            touching = obj[obj.intersects(row.geometry)]
            data = scene.build(row, leg, mod, cores, touching, images, p)
            data["batch"] = batch
            path = out / "scene.json"
            with io.atomic_path(path) as tmp:
                tmp.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))
            step.output(path)
        log.info(
            "scene %s %s: %d legacy, %d 3DEP, %d core points, %d objects, 3DEP %s, NAIP %d",
            args.site, sid, leg["x"].size, mod["x"].size, len(cores), len(touching), batch, year,
        )  # fmt: skip


def _overview(args):
    from PIL import Image

    from ..common import grid
    from ..common.crs import PROJECT_CRS, to_lonlat
    from . import overview

    _, params = _site_params(args.site)
    p = params.section("viz_label.overview")
    geom = config.site_geometry(args.site)
    out = paths.site_dir(args.site) / "viz"
    change = paths.site_dir(args.site) / "change"
    with Step("viz_label.overview", out_dir=out, params=p, unit=args.site) as step:
        acc = overview.Accumulator(geom.bounds, p["res_m"])
        for cell in grid.cells_for_geometry(geom):
            f = change / "m3c2" / cell / "m3c2.parquet"
            if not f.exists():
                raise FileNotFoundError(f"{f}: run `ilcd change m3c2 {args.site}` for every site cell first")
            step.input(f, "m3c2")
            t = io.read_table(f, "m3c2", columns=["x_m", "y_m", "z_m", "d_m", "sig"]).to_pandas()
            acc.add(*(t[c].to_numpy() for c in ("x_m", "y_m", "z_m", "d_m")), t["sig"].to_numpy(bool))
        png = out / "overview.png"
        with io.atomic_path(png) as tmp:
            Image.fromarray(acc.rgba(p["ramp_m"]), "RGBA").save(tmp, format="PNG", optimize=True)
        step.output(png)
        x1, y1 = acc.xmin + acc.w * acc.res, acc.ymin + acc.h * acc.res
        lon, lat = to_lonlat([acc.xmin, x1], [acc.ymin, y1])
        doc = {
            "png": "overview.png",
            "crs": PROJECT_CRS,
            "xmin": acc.xmin,
            "ymin": acc.ymin,
            "width": acc.w,
            "height": acc.h,
            "res_m": acc.res,
            "ramp_m": p["ramp_m"],
            "lonlat": [float(lon[0]), float(lat[0]), float(lon[1]), float(lat[1])],
        }
        j = out / "overview.json"
        with io.atomic_path(j) as tmp:
            tmp.write_text(json.dumps(doc, indent=1) + "\n")
        step.output(j)
    print(f"{args.site}: overview {acc.w} x {acc.h} px at {acc.res} m")


def _page(args):
    from ..common import contracts
    from . import page

    sc, params = _site_params(args.site)
    out = paths.site_dir(args.site) / "viz"
    with Step("viz_label.page", out_dir=out, params={}, unit=args.site) as step:
        for f, c in ((_objects_path(args.site), "objects"), (_scenes_path(args.site), "scenes")):
            step.input(f, c)
        step.input(out / "overview.json", hash_file=True)
        obj = io.read_vector(_objects_path(args.site), "objects")
        scenes = io.read_vector(_scenes_path(args.site), "scenes")
        missing = [s for s in scenes["scene_id"] if not (out / "scenes" / s / "scene.json").exists()]
        if missing:
            raise FileNotFoundError(f"scene data missing for {missing[:5]}...: run `ilcd viz_label scene-data` first")
        site = {**config.load_site(args.site)["features"][0]["properties"], "scope": sc}
        mp = params.section("change.m3c2")
        man = json.loads((paths.site_dir(args.site) / "change" / "change.objects.manifest.json").read_text())
        info = {
            "m3c2": {k: mp[k] for k in ("core_res_m", "cylinder_radius_m", "normal_radii_m", "max_distance_m")},
            "objects": params.section("change.objects"),
            "review": params.section("viz_label.review"),
            "code": man["code"],
        }
        data = page.site_data(
            site,
            obj,
            scenes,
            json.loads((out / "overview.json").read_text()),
            info,
            contracts.get("labels").column("class").enum,
        )
        html = out / "index.html"
        with io.atomic_path(html) as tmp:
            tmp.write_text(page.render(data))
        step.output(html)
    print(f"{args.site}: {html} ({html.stat().st_size / 1e6:.1f} MB), {len(obj)} objects, {len(scenes)} scenes")


def _import_labels(args):
    from . import labels

    path = paths.site_dir(args.site) / "labels" / "labels.geojson"
    obj = io.read_vector(_objects_path(args.site), "objects")
    new = labels.from_page(json.loads(open(args.file).read()), obj)
    old = io.read_json(path, "labels") if path.exists() else None
    merged = labels.merge(old, new)
    path.parent.mkdir(parents=True, exist_ok=True)
    io.write_json(merged, path, "labels")
    print(f"{args.site}: {len(new)} labels imported, {len(merged['features'])} in {path}")


def register(sub):
    p = sub.add_parser("viz_label", help="site page with 3-D review scenes; labels").add_subparsers(
        dest="sub", required=True
    )
    s = p.add_parser("scenes", help="review sample of a site's objects and their scene squares")
    s.add_argument("site")
    s.set_defaults(fn=_scenes)
    s = p.add_parser("scene-ids", help="print the scene ids of a site")
    s.add_argument("site")
    s.set_defaults(fn=_scene_ids)
    s = p.add_parser("scene-data", help="points, grids and orthophotos of scenes")
    s.add_argument("site")
    s.add_argument("ids", nargs="*")
    s.add_argument("--from-file")
    s.add_argument("--chunk")
    s.add_argument("--skip-existing", action="store_true", help="skip scenes whose data is current")
    s.set_defaults(fn=_scene_data)
    s = p.add_parser("overview", help="site overview image from the M3C2 cells")
    s.add_argument("site")
    s.set_defaults(fn=_overview)
    s = p.add_parser("page", help="the site page, index.html")
    s.add_argument("site")
    s.set_defaults(fn=_page)
    s = p.add_parser("import-labels", help="merge labels copied from the page into the site's labels")
    s.add_argument("site")
    s.add_argument("file")
    s.set_defaults(fn=_import_labels)
