"""File contracts: load the specs in ilcd/schemas/ and check data against them (docs/design.md section 8).

A spec is `<name>.v<version>.json` with a `kind`:
  table    Parquet; `columns` lists every column (no others allowed), `key` must be unique
  vector   GeoParquet; as table, plus a WKB `geometry` column and a `geometry` block (type, crs)
  geojson  GeoJSON FeatureCollection in EPSG:4326; `columns` describe feature properties
  json     JSON document validated by the JSON Schema in `schema`
  raster   GeoTIFF; `raster` gives crs/resolution/size, `bands` the band names and dtypes

Problems are collected and raised together as one ContractError, so a writer sees everything wrong at once.
"""

import json
from dataclasses import dataclass, field
from functools import cache
from importlib import resources

import pyarrow as pa
import pyarrow.compute as pc

KINDS = ("table", "vector", "geojson", "json", "raster")
TYPES = {
    "string": pa.string(),
    "bool": pa.bool_(),
    "int8": pa.int8(),
    "int16": pa.int16(),
    "int32": pa.int32(),
    "int64": pa.int64(),
    "uint8": pa.uint8(),
    "uint16": pa.uint16(),
    "float32": pa.float32(),
    "float64": pa.float64(),
    "list<string>": pa.list_(pa.string()),
    "geometry": pa.binary(),
}
METADATA_KEY = b"ilcd:contract"


class ContractError(ValueError):
    def __init__(self, contract: str, problems: list[str]):
        self.contract, self.problems = contract, problems
        super().__init__(f"{contract}: " + "; ".join(problems))


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = False
    unit: str | None = None
    enum: tuple | None = None
    description: str = ""


@dataclass(frozen=True)
class Contract:
    name: str
    version: int
    kind: str
    owner: str
    status: str
    description: str
    path: tuple[str, ...]
    key: tuple[str, ...] = ()
    columns: tuple[Column, ...] = ()
    geometry: dict | None = None
    schema: dict | None = None
    raster: dict | None = None
    bands: tuple[dict, ...] = ()
    spec: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def tag(self) -> str:
        return f"{self.name}.v{self.version}"

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"{self.tag} has no column {name!r}")

    def arrow_schema(self) -> pa.Schema:
        """Arrow schema of a table/vector contract, with the contract tag in the metadata."""
        if self.kind not in ("table", "vector"):
            raise TypeError(f"{self.tag} is a {self.kind}, not a table")
        return pa.schema(
            [pa.field(c.name, TYPES[c.type], nullable=c.nullable) for c in self.columns],
            metadata={METADATA_KEY: self.tag.encode()},
        )

    def empty_table(self) -> pa.Table:
        return self.arrow_schema().empty_table()

    # validation -----------------------------------------------------------------------------------------------

    def check_table(self, table: pa.Table, columns: list[str] | None = None) -> None:
        """Validate a table/vector. With `columns`, only those columns are checked (a projected read)."""
        if self.kind not in ("table", "vector"):
            raise TypeError(f"{self.tag} is a {self.kind}, not a table")
        problems = []
        wanted = [c for c in self.columns if columns is None or c.name in columns]
        names = table.column_names
        if columns is None:
            extra = [n for n in names if n not in {c.name for c in self.columns}]
            if extra:
                problems.append(f"unexpected columns {extra}")
        for c in wanted:
            if c.name not in names:
                problems.append(f"missing column {c.name}")
                continue
            col = table.column(c.name)
            if not _type_ok(col.type, c.type):
                problems.append(f"column {c.name} is {col.type}, expected {c.type}")
                continue
            if not c.nullable and col.null_count:
                problems.append(f"column {c.name} has {col.null_count} nulls")
            if c.type in ("float32", "float64") and not c.nullable and len(col):
                n_nan = pc.sum(pc.is_nan(col).cast(pa.int64())).as_py() or 0
                if n_nan:
                    problems.append(f"column {c.name} has {n_nan} NaN values (declare nullable and use nulls)")
            if c.enum is not None and len(col):
                values = pc.unique(col.cast(pa.string()) if pa.types.is_dictionary(col.type) else col).to_pylist()
                bad = sorted({v for v in values if v is not None} - set(c.enum))
                if bad:
                    problems.append(f"column {c.name} has values {bad[:5]} outside {list(c.enum)}")
        if self.key and all(k in names for k in self.key) and table.num_rows:
            if len(self.key) == 1:
                n_unique = pc.count_distinct(table.column(self.key[0])).as_py()
            else:
                joined = pc.binary_join_element_wise(*[table.column(k).cast(pa.string()) for k in self.key], "\x1f")
                n_unique = pc.count_distinct(joined).as_py()
            if n_unique != table.num_rows:
                problems.append(f"key {list(self.key)} not unique ({table.num_rows - n_unique} duplicates)")
        if self.kind == "vector" and columns is None:
            problems += _check_geo_metadata(table.schema.metadata or {}, self.geometry)
        if problems:
            raise ContractError(self.tag, problems)

    def check_json(self, obj) -> None:
        if self.kind != "json":
            raise TypeError(f"{self.tag} is a {self.kind}, not a JSON document")
        import jsonschema

        v = jsonschema.Draft202012Validator(self.schema)
        problems = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(obj)]
        if problems:
            raise ContractError(self.tag, problems)

    def check_geojson(self, obj) -> None:
        """Validate a FeatureCollection: geometry type, property names/types/enums, key uniqueness."""
        if self.kind != "geojson":
            raise TypeError(f"{self.tag} is a {self.kind}, not GeoJSON")
        problems = []
        if not isinstance(obj, dict) or obj.get("type") != "FeatureCollection":
            raise ContractError(self.tag, ["not a GeoJSON FeatureCollection"])
        gtype = (self.geometry or {}).get("type")
        allowed = _geometry_family(gtype) if gtype else None
        cols = {c.name: c for c in self.columns}
        keys = []
        for i, f in enumerate(obj.get("features", [])):
            g = (f.get("geometry") or {}).get("type")
            if allowed and g not in allowed:
                problems.append(f"feature {i}: geometry {g}, expected {gtype}")
            props = f.get("properties") or {}
            extra = sorted(set(props) - set(cols))
            if extra:
                problems.append(f"feature {i}: unexpected properties {extra}")
            for c in cols.values():
                v = props.get(c.name)
                if v is None:
                    if not c.nullable:
                        problems.append(f"feature {i}: missing {c.name}")
                    continue
                if not _json_type_ok(v, c.type):
                    problems.append(f"feature {i}: {c.name}={v!r} is not {c.type}")
                elif c.enum is not None and v not in c.enum:
                    problems.append(f"feature {i}: {c.name}={v!r} outside {list(c.enum)}")
            keys.append(tuple(props.get(k) for k in self.key))
        if self.key and len(set(keys)) != len(keys):
            problems.append(f"key {list(self.key)} not unique")
        if problems:
            raise ContractError(self.tag, problems[:50])

    def check_raster(self, path) -> None:
        if self.kind != "raster":
            raise TypeError(f"{self.tag} is a {self.kind}, not a raster")
        import rasterio

        from .crs import is_project

        r, problems = self.raster, []
        with rasterio.open(path) as ds:
            if ds.crs is None or not is_project(ds.crs.to_wkt()):
                problems.append(f"crs {ds.crs}, expected {r['crs']}")
            if abs(ds.res[0] - r["resolution_m"]) > 1e-9 or abs(ds.res[1] - r["resolution_m"]) > 1e-9:
                problems.append(f"resolution {ds.res}, expected {r['resolution_m']}")
            if "width" in r and (ds.width, ds.height) != (r["width"], r["height"]):
                problems.append(f"size {ds.width}x{ds.height}, expected {r['width']}x{r['height']}")
            want = [b["name"] for b in self.bands]
            got = list(ds.descriptions)
            if got != want:
                problems.append(f"bands {got}, expected {want}")
            for b, dt in zip(self.bands, ds.dtypes, strict=False):
                if dt != b["dtype"]:
                    problems.append(f"band {b['name']} is {dt}, expected {b['dtype']}")
        if problems:
            raise ContractError(self.tag, problems)


def _type_ok(actual: pa.DataType, declared: str) -> bool:
    want = TYPES[declared]
    if declared == "string":
        return (
            pa.types.is_string(actual)
            or pa.types.is_large_string(actual)
            or (pa.types.is_dictionary(actual) and pa.types.is_string(actual.value_type))
        )
    if declared == "list<string>":
        return (pa.types.is_list(actual) or pa.types.is_large_list(actual)) and pa.types.is_string(actual.value_type)
    if declared == "geometry":
        return pa.types.is_binary(actual) or pa.types.is_large_binary(actual)
    return actual == want


def _json_type_ok(v, declared: str) -> bool:
    if declared == "string":
        return isinstance(v, str)
    if declared == "bool":
        return isinstance(v, bool)
    if declared.startswith(("int", "uint")):
        return isinstance(v, int) and not isinstance(v, bool)
    if declared.startswith("float"):
        return isinstance(v, int | float) and not isinstance(v, bool)
    if declared == "list<string>":
        return isinstance(v, list) and all(isinstance(x, str) for x in v)
    return False


def _geometry_family(declared: str) -> set[str]:
    """A declared geometry type admits its single and multi forms (Polygon <-> MultiPolygon)."""
    single = declared.removeprefix("Multi")
    return {single, f"Multi{single}"}


def _check_geo_metadata(meta: dict, geometry: dict) -> list[str]:
    """GeoParquet 'geo' metadata: primary column 'geometry', WKB, project CRS, declared geometry type."""
    from .crs import is_project

    raw = meta.get(b"geo")
    if raw is None:
        return ["no GeoParquet 'geo' metadata"]
    geo = json.loads(raw)
    col = geo.get("columns", {}).get(geo.get("primary_column", ""), {})
    problems = []
    if geo.get("primary_column") != "geometry":
        problems.append(f"primary geometry column is {geo.get('primary_column')!r}, expected 'geometry'")
    if col.get("encoding", "WKB") != "WKB":
        problems.append(f"geometry encoding {col.get('encoding')}, expected WKB")
    crs = col.get("crs")
    if crs is None or not is_project(json.dumps(crs) if isinstance(crs, dict) else crs):
        problems.append(f"geometry crs is not {geometry['crs']}")
    types = col.get("geometry_types") or []
    allowed = _geometry_family(geometry["type"])
    if types and not set(types) <= allowed:
        problems.append(f"geometry types {types}, expected {geometry['type']}")
    return problems


# loading ------------------------------------------------------------------------------------------------------------


def _parse(spec: dict) -> Contract:
    path = spec["path"]
    cols = tuple(
        Column(
            name=c["name"],
            type=c["type"],
            nullable=c.get("nullable", False),
            unit=c.get("unit"),
            enum=tuple(c["enum"]) if "enum" in c else None,
            description=c.get("description", ""),
        )
        for c in spec.get("columns", ())
    )
    return Contract(
        name=spec["contract"],
        version=spec["version"],
        kind=spec["kind"],
        owner=spec["owner"],
        status=spec["status"],
        description=spec["description"],
        path=tuple(path) if isinstance(path, list) else (path,),
        key=tuple(spec.get("key", ())),
        columns=cols,
        geometry=spec.get("geometry"),
        schema=spec.get("schema"),
        raster=spec.get("raster"),
        bands=tuple(spec.get("bands", ())),
        spec=spec,
    )


@cache
def all_contracts() -> dict[str, Contract]:
    """Every contract, keyed by tag (`passes.v1`)."""
    out = {}
    for f in resources.files("ilcd.schemas").iterdir():
        if f.name.endswith(".json"):
            c = _parse(json.loads(f.read_text()))
            if f.name != f"{c.tag}.json":
                raise ContractError(c.tag, [f"file name {f.name} does not match the contract tag"])
            out[c.tag] = c
    return out


def get(name: str, version: int | None = None) -> Contract:
    """A contract by name, latest version unless one is given. `name` may also be a full tag."""
    if ".v" in name:
        name, version = name.split(".v")[0], int(name.split(".v")[1])
    versions = [c for c in all_contracts().values() if c.name == name]
    if not versions:
        raise KeyError(f"no contract {name!r}")
    if version is None:
        return max(versions, key=lambda c: c.version)
    for c in versions:
        if c.version == version:
            return c
    raise KeyError(f"no contract {name}.v{version}")
