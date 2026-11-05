"""Reading and writing contract files (docs/design.md section 8).

Every write validates against the contract first and replaces the target atomically (temporary file in the same
directory, then rename), so a crashed job never leaves a half-written output that looks finished. Every read checks
the contract tag stored in the file and validates the data.
"""

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import contracts
from .contracts import METADATA_KEY, ContractError


def sha256_file(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


@contextmanager
def atomic_path(path):
    """Yield a temporary path next to `path`; rename it onto `path` only if the block succeeds."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        yield Path(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _geo_metadata(crs_projjson: dict | None, geometry_types: list[str]) -> bytes:
    """GeoParquet 1.0 'geo' metadata for a single WKB column named 'geometry'."""
    col = {"encoding": "WKB", "geometry_types": geometry_types}
    if crs_projjson is not None:
        col["crs"] = crs_projjson
    return json.dumps({"version": "1.0.0", "primary_column": "geometry", "columns": {"geometry": col}}).encode()


def _contract(c) -> contracts.Contract:
    return c if isinstance(c, contracts.Contract) else contracts.get(c)


# tables and vectors -------------------------------------------------------------------------------------------------


def to_arrow(data, contract) -> pa.Table:
    """pyarrow Table, pandas DataFrame or geopandas GeoDataFrame -> Table cast to the contract's column order and
    types, with the contract tag (and GeoParquet metadata for vectors) attached."""
    c = _contract(contract)
    if hasattr(data, "geometry") and hasattr(data, "to_arrow"):  # GeoDataFrame
        if data.geometry.name != "geometry":
            raise ContractError(c.tag, [f"geometry column is {data.geometry.name!r}, expected 'geometry'"])
        table = pa.table(data.to_arrow(index=False, geometry_encoding="WKB"))
        crs = data.crs.to_json_dict() if data.crs is not None else None
        types = sorted(set(data.geometry.geom_type.dropna()))
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), b"geo": _geo_metadata(crs, types)})
    elif isinstance(data, pa.Table):
        table = data
    else:  # pandas
        table = pa.Table.from_pandas(data, preserve_index=False)
    missing = [col.name for col in c.columns if col.name not in table.column_names]
    extra = [n for n in table.column_names if n not in {col.name for col in c.columns}]
    if missing or extra:
        raise ContractError(
            c.tag, [p for p in (missing and f"missing columns {missing}", extra and f"unexpected columns {extra}") if p]
        )
    geo = (table.schema.metadata or {}).get(b"geo")
    table = table.select([col.name for col in c.columns])
    try:
        table = table.cast(c.arrow_schema().remove_metadata(), safe=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as e:
        c.check_table(table)  # reports the precise column problems
        raise ContractError(c.tag, [f"cannot cast to contract types: {e}"]) from e
    meta = {METADATA_KEY: c.tag.encode()}
    if geo is not None:
        meta[b"geo"] = geo
    return table.replace_schema_metadata(meta)


def write_table(data, path, contract) -> Path:
    c = _contract(contract)
    table = to_arrow(data, c)
    c.check_table(table)
    with atomic_path(path) as tmp:
        pq.write_table(table, tmp, compression="zstd")
    return Path(path)


def write_empty_table(path, contract) -> Path:
    """A valid zero-row output (status `empty`). Vectors get minimal GeoParquet metadata."""
    c = _contract(contract)
    table = c.empty_table()
    if c.kind == "vector":
        from pyproj import CRS

        geo = _geo_metadata(CRS(c.geometry["crs"]).to_json_dict(), [])
        table = table.replace_schema_metadata({**table.schema.metadata, b"geo": geo})
    c.check_table(table)
    with atomic_path(path) as tmp:
        pq.write_table(table, tmp, compression="zstd")
    return Path(path)


def read_table(path, contract, columns: list[str] | None = None) -> pa.Table:
    c = _contract(contract)
    table = pq.read_table(path, columns=columns)
    tag = (pq.read_schema(path).metadata or {}).get(METADATA_KEY, b"").decode()
    if tag != c.tag:
        raise ContractError(c.tag, [f"{path} was written as {tag or 'no contract'}"])
    c.check_table(table, columns)
    return table


def read_vector(path, contract, columns: list[str] | None = None):
    """GeoDataFrame of a vector contract file (validated first)."""
    import geopandas as gpd

    c = _contract(contract)
    if c.kind != "vector":
        raise TypeError(f"{c.tag} is not a vector contract")
    read_table(path, c, columns)
    return gpd.read_parquet(path, columns=columns)


# JSON and GeoJSON ---------------------------------------------------------------------------------------------------


def write_json(obj, path, contract) -> Path:
    c = _contract(contract)
    if c.kind == "geojson":
        c.check_geojson(obj)
    else:
        c.check_json(obj)
    with atomic_path(path) as tmp:
        tmp.write_text(json.dumps(obj, indent=1, sort_keys=False, allow_nan=False) + "\n")
    return Path(path)


def read_json(path, contract):
    c = _contract(contract)
    text = Path(path).read_text()
    if Path(path).suffix in (".yaml", ".yml"):
        import yaml

        obj = yaml.safe_load(text) or {}
    else:
        obj = json.loads(text)
    if c.kind == "geojson":
        c.check_geojson(obj)
    else:
        c.check_json(obj)
    return obj
