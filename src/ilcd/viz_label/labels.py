"""Labels from the site page into contract `labels` (design.md section 7.2).

The page copies out a FeatureCollection with the label properties and no geometry; the geometry is the labelled
object's outline, filled in here from `objects`. label_id = <object_id>:<annotator>; a label replaces an earlier one
with the same id.
"""

from shapely.geometry import mapping

from ..common import contracts, crs


class LabelError(ValueError):
    pass


def label_id(object_id: str, annotator: str) -> str:
    return f"{object_id}:{annotator}"


def from_page(doc: dict, objects) -> list[dict]:
    """Features of contract `labels` from a page export. objects: GeoDataFrame of contract `objects`."""
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise LabelError("not a GeoJSON FeatureCollection")
    geom = dict(zip(objects["object_id"], objects.to_crs(crs.GEOGRAPHIC).geometry, strict=True))
    classes = contracts.get("labels").column("class").enum
    out, problems = [], []
    for i, f in enumerate(doc.get("features", [])):
        p = dict(f.get("properties") or {})
        oid, ann = p.get("object_id"), (p.get("annotator") or "").strip()
        if oid not in geom:
            problems.append(f"feature {i}: unknown object {oid!r}")
            continue
        if not ann:
            problems.append(f"feature {i}: no annotator")
            continue
        if p.get("class") not in classes:
            problems.append(f"feature {i}: class {p.get('class')!r} not one of {list(classes)}")
            continue
        p.update(label_id=label_id(oid, ann), annotator=ann)
        out.append({"type": "Feature", "properties": p, "geometry": mapping(geom[oid])})
    if problems:
        raise LabelError("; ".join(problems[:20]))
    return out


def merge(old: dict | None, new: list[dict]) -> dict:
    """FeatureCollection of old features with new ones replacing those of the same label_id, sorted by id."""
    by = {f["properties"]["label_id"]: f for f in (old or {}).get("features", [])}
    by.update({f["properties"]["label_id"]: f for f in new})
    return {"type": "FeatureCollection", "features": [by[k] for k in sorted(by)]}
