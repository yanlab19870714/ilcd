"""Every location in the data store and the config directory (docs/design.md sections 7 and 9).

No module builds a data path by hand. The data store root is $ILCD_DATA_ROOT; the config directory is
$ILCD_CONFIG_DIR, or the `config/` directory of the repository the package is installed from.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

from . import ids

OT_PREFIX = "/pc-bulk/"
CATALOGS = ("legacy_files", "legacy_headers", "deliveries", "modern_batches")


class ConfigurationError(RuntimeError):
    """A required environment variable or directory is missing."""


def data_root() -> Path:
    v = os.environ.get("ILCD_DATA_ROOT")
    if not v:
        raise ConfigurationError("ILCD_DATA_ROOT is not set (see docs/runbook.md)")
    p = Path(v)
    if not p.is_dir():
        raise ConfigurationError(f"ILCD_DATA_ROOT={v} is not a directory")
    return p


def config_dir() -> Path:
    v = os.environ.get("ILCD_CONFIG_DIR")
    p = Path(v) if v else Path(__file__).resolve().parents[3] / "config"
    if not (p / "regions").is_dir():
        raise ConfigurationError(f"no config directory with regions/ at {p}; set ILCD_CONFIG_DIR")
    return p


def relative(path: Path) -> str:
    """Path relative to the data root when inside it (as recorded in manifests), else absolute."""
    path = Path(path).resolve()
    try:
        return str(path.relative_to(data_root().resolve()))
    except (ValueError, ConfigurationError):
        return str(path)


# raw caches -----------------------------------------------------------------------------------------------------


def cache_legacy(url: str) -> Path:
    """Local copy of an OT legacy file, mirroring the bucket path (IN_2011_2013/<zone>/<folder>/<name>)."""
    path = urlparse(url).path
    if OT_PREFIX not in path:
        raise ValueError(f"not an OT pc-bulk URL: {url}")
    return data_root() / "cache" / "legacy" / path.split(OT_PREFIX, 1)[1]


def cache_modern(batch: str, cell: str) -> Path:
    ids.parse_cell(cell)
    return data_root() / "cache" / "modern" / batch / f"{cell}.laz"


def cache_ancillary(source: str) -> Path:
    return data_root() / "cache" / "ancillary" / source


# statewide catalogs ---------------------------------------------------------------------------------------------


def catalog(name: str) -> Path:
    if name not in CATALOGS:
        raise ValueError(f"unknown catalog {name!r}; one of {CATALOGS}")
    return data_root() / "statewide" / "catalog" / f"{name}.parquet"


def delivery_dir(delivery: str) -> Path:
    ids.parse_delivery(delivery)
    return data_root() / "statewide" / "deliveries" / delivery


# per file, per cell ---------------------------------------------------------------------------------------------


def file_dir(file: str) -> Path:
    ids.parse_file(file)
    return data_root() / "files" / file


def cell_dir(cell: str) -> Path:
    ids.parse_cell(cell)
    return data_root() / "cells" / cell


# per scope, per site --------------------------------------------------------------------------------------------


def scope_dir(scope: str) -> Path:
    """regions/<name>/ for `region:<name>`, statewide/scope/ for `statewide`."""
    region = ids.parse_scope(scope)
    return data_root() / ("statewide/scope" if region is None else f"regions/{region}")


def model_dir(scope: str, method: str | None = None) -> Path:
    """align/model/ of a scope, or baselines/<method>/model/ for a baseline."""
    base = scope_dir(scope)
    return base / "align" / "model" if method is None else base / "baselines" / method / "model"


def site_dir(site: str) -> Path:
    ids.check_region(site)  # same naming rule as regions
    return data_root() / "sites" / site


# config files ---------------------------------------------------------------------------------------------------


def region_config(name: str) -> Path:
    ids.check_region(name)
    return config_dir() / "regions" / f"{name}.yaml"


def site_config(name: str) -> Path:
    ids.check_region(name)
    return config_dir() / "sites" / f"{name}.geojson"


def deliveries_config() -> Path:
    return config_dir() / "deliveries.yaml"
