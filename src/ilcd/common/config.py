"""Parameters and user configuration (docs/design.md section 9).

`defaults.yaml` (shipped with the package) holds every tunable parameter. A region may override parameters under
`params:` with the same nesting; overriding a key that does not exist, or changing a value's type, is an error.
"""

import copy
import hashlib
import json
from dataclasses import dataclass
from functools import cache
from importlib import resources

import yaml

from . import contracts, ids, paths
from .io import read_json


class ParamError(KeyError):
    pass


@cache
def _defaults() -> dict:
    return yaml.safe_load(resources.files("ilcd.config").joinpath("defaults.yaml").read_text())


def defaults() -> dict:
    return copy.deepcopy(_defaults())


def merge(base: dict, overrides: dict, where: str = "") -> dict:
    """base updated with overrides; every override key must exist in base with a value of the same type."""
    out = copy.deepcopy(base)
    for k, v in (overrides or {}).items():
        here = f"{where}.{k}" if where else k
        if k not in out:
            raise ParamError(f"unknown parameter {here}")
        if isinstance(out[k], dict):
            if not isinstance(v, dict):
                raise ParamError(f"parameter {here} is a group; give its keys")
            out[k] = merge(out[k], v, here)
        else:
            if not _same_type(out[k], v):
                raise ParamError(f"parameter {here}: {v!r} is not of the default's type ({type(out[k]).__name__})")
            out[k] = v
    return out


def _same_type(default, value) -> bool:
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(default, bool) and isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, int | float)
    if isinstance(default, list):
        return isinstance(value, list)
    if default is None:
        return True
    return isinstance(value, type(default))


class Params:
    """Read-only nested parameters with dotted access: p["flightlines.gap_s"] or p.section("flightlines")."""

    def __init__(self, tree: dict):
        self._tree = tree

    def __getitem__(self, dotted: str):
        node = self._tree
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise ParamError(f"no parameter {dotted}")
            node = node[part]
        return copy.deepcopy(node)

    def section(self, name: str) -> dict:
        v = self[name]
        if not isinstance(v, dict):
            raise ParamError(f"{name} is not a parameter group")
        return v

    def as_dict(self) -> dict:
        return copy.deepcopy(self._tree)

    def sha256(self, section: str | None = None) -> str:
        tree = self._tree if section is None else self.section(section)
        return hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Region:
    name: str
    description: str
    footprint: dict
    params: Params
    notes: tuple[str, ...]

    @property
    def scope(self) -> str:
        return ids.scope(self.name)


def load_region(name: str) -> Region:
    p = paths.region_config(name)
    raw = read_json(p, contracts.get("region"))
    if raw["name"] != name:
        raise ValueError(f"{p}: name {raw['name']!r} does not match the file name")
    return Region(
        name=name,
        description=raw.get("description", ""),
        footprint=raw["footprint"],
        params=Params(merge(_defaults(), raw.get("params", {}))),
        notes=tuple(raw.get("notes", ())),
    )


def default_params() -> Params:
    """Parameters for steps that are not tied to a region (per-file and per-cell steps, statewide scope)."""
    return Params(defaults())


def region_names() -> list[str]:
    return sorted(f.stem for f in (paths.config_dir() / "regions").glob("*.yaml"))


def site_names() -> list[str]:
    return sorted(f.stem for f in (paths.config_dir() / "sites").glob("*.geojson"))


def load_site(name: str) -> dict:
    """Validated site FeatureCollection (one feature)."""
    obj = read_json(paths.site_config(name), contracts.get("site"))
    if len(obj["features"]) != 1 or obj["features"][0]["properties"]["site"] != name:
        raise ValueError(f"site file {name}.geojson must hold exactly one feature with site={name!r}")
    return obj


def site_geometry(name: str):
    """The site polygon in the project CRS (site files are EPSG:4326, RFC 7946)."""
    import geopandas as gpd
    from shapely.geometry import shape

    from .crs import GEOGRAPHIC, PROJECT_CRS

    g = shape(load_site(name)["features"][0]["geometry"])
    return gpd.GeoSeries([g], crs=GEOGRAPHIC).to_crs(PROJECT_CRS).iloc[0]


def site_scope(name: str) -> str:
    """The scope whose model corrects the site (statewide when the site names none)."""
    return load_site(name)["features"][0]["properties"].get("scope") or "statewide"


def load_delivery_overrides() -> dict:
    p = paths.deliveries_config()
    return read_json(p, contracts.get("delivery_overrides")) if p.exists() else {}
