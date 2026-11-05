"""Identifiers used across ilcd (docs/design.md section 4).

Every identifier is a deterministic string with one constructor and one parser here, so no module formats or splits
an id by hand. Parsers raise ValueError on malformed input.
"""

import math
import re

ZONE_DIRS = {"East_IN": "E", "West_IN": "W"}
ZONE_NAMES = {v: k for k, v in ZONE_DIRS.items()}

# One-letter codes in patch ids; the set of kinds is the enum of the patches contract.
PATCH_KINDS = {
    "road": "R",
    "hard_other": "H",
    "roof_flat": "F",
    "roof_sloped": "S",
    "natural_flat": "N",
    "natural_slope": "G",
}
_KIND_OF_CODE = {v: k for k, v in PATCH_KINDS.items()}

_NAME = r"[A-Za-z0-9_]+"
_DELIVERY = re.compile(rf"^([EW])_({_NAME}(?:-{_NAME})?)$")  # nested folders: IN_W_2013/Dubois -> IN_W_2013-Dubois
_FILE = re.compile(r"^([EW])\.([A-Za-z0-9_]+)$")
_PASS = re.compile(r"^([EW]\.[A-Za-z0-9_]+)#([1-9][0-9]*)$")
_CELL = re.compile(r"^E([0-9]{4})N([0-9]{4})$")
_PATCH = re.compile(r"^(E[0-9]{4}N[0-9]{4})\.([A-Z])([0-9]{5})$")
_DOMAIN = re.compile(rf"^(abs|wk:[EW]_{_NAME}(?:-{_NAME})?)$")
_LINE = re.compile(rf"^(abs|wk:[EW]_{_NAME}(?:-{_NAME})?)@(-?[0-9]+\.[0-9])(?:#([2-9]|[1-9][0-9]+))?$")
_BLOCK = re.compile(rf"^(abs|wk:[EW]_{_NAME}(?:-{_NAME})?)@(-?[0-9]+)#B$")
_REGION = re.compile(r"^[a-z][a-z0-9_]*$")

CELL_KM = 1


def _match(pattern, value, what):
    m = pattern.match(value) if isinstance(value, str) else None
    if m is None:
        raise ValueError(f"not a {what}: {value!r}")
    return m


# deliveries and files -------------------------------------------------------------------------------------------


def delivery_id(zone_dir: str, folder: str) -> str:
    """OT folder below the zone -> delivery id: `West_IN`, `Monroe` -> `W_Monroe`;
    `West_IN`, `IN_W_2013/Dubois` -> `W_IN_W_2013-Dubois` (the 2013 batch has county subfolders)."""
    if zone_dir not in ZONE_DIRS:
        raise ValueError(f"unknown OT zone folder {zone_dir!r}")
    return _match(_DELIVERY, f"{ZONE_DIRS[zone_dir]}_{folder.replace('/', '-')}", "delivery_id").group(0)


def parse_delivery(value: str) -> tuple[str, str]:
    """`W_IN_W_2013-Dubois` -> (`West_IN`, `IN_W_2013/Dubois`)."""
    m = _match(_DELIVERY, value, "delivery_id")
    return ZONE_NAMES[m.group(1)], m.group(2).replace("-", "/")


def file_id(zone_dir: str, filename: str) -> str:
    """`West_IN`, `ot_in2011_31501470_12_1.laz` -> `W.ot_in2011_31501470_12_1`."""
    if zone_dir not in ZONE_DIRS:
        raise ValueError(f"unknown OT zone folder {zone_dir!r}")
    stem = filename[:-4] if filename.lower().endswith(".laz") else filename
    return _match(_FILE, f"{ZONE_DIRS[zone_dir]}.{stem}", "file_id").group(0)


def parse_file(value: str) -> tuple[str, str]:
    """`W.ot_in2011_31501470_12_1` -> (`West_IN`, `ot_in2011_31501470_12_1.laz`)."""
    m = _match(_FILE, value, "file_id")
    return ZONE_NAMES[m.group(1)], m.group(2) + ".laz"


# passes, lines, blocks ------------------------------------------------------------------------------------------


def pass_id(file: str, k: int) -> str:
    """k-th pass (1-based, in time order) of a file."""
    _match(_FILE, file, "file_id")
    if int(k) != k or k < 1:
        raise ValueError(f"pass index must be a positive integer, got {k!r}")
    return f"{file}#{int(k)}"


def parse_pass(value: str) -> tuple[str, int]:
    m = _match(_PASS, value, "pass_id")
    return m.group(1), int(m.group(2))


def time_domain(gps_time_type: str, delivery: str, gps_week: int | None) -> str:
    """`abs` when times are (or can be converted to) adjusted standard; `wk:<delivery>` otherwise."""
    _match(_DELIVERY, delivery, "delivery_id")
    if gps_time_type == "adjusted_standard" or (gps_time_type == "week_seconds" and gps_week is not None):
        return "abs"
    if gps_time_type == "week_seconds":
        return f"wk:{delivery}"
    raise ValueError(f"unknown gps_time_type {gps_time_type!r}")


def check_time_domain(value: str) -> str:
    return _match(_DOMAIN, value, "time_domain").group(0)


def line_id(domain: str, t_start_s: float, k: int = 1) -> str:
    """k > 1 disambiguates lines of one scope that start in the same 0.1 s (k-th in pass_id order)."""
    check_time_domain(domain)
    if not math.isfinite(t_start_s):
        raise ValueError("line start time must be finite")
    if int(k) != k or k < 1:
        raise ValueError(f"line index must be a positive integer, got {k!r}")
    return f"{domain}@{t_start_s:.1f}" + (f"#{int(k)}" if k > 1 else "")


def parse_line(value: str) -> tuple[str, float]:
    m = _match(_LINE, value, "line_id")
    return m.group(1), float(m.group(2))


def block_id(domain: str, t_start_s: float) -> str:
    check_time_domain(domain)
    if not math.isfinite(t_start_s):
        raise ValueError("block start time must be finite")
    return f"{domain}@{math.floor(t_start_s):d}#B"


def parse_block(value: str) -> tuple[str, int]:
    m = _match(_BLOCK, value, "block_id")
    return m.group(1), int(m.group(2))


# cells and patches ----------------------------------------------------------------------------------------------


def cell_id(e_km: int, n_km: int) -> str:
    """Cell whose lower-left corner is (e_km, n_km) km in EPSG:6345."""
    if not (0 <= e_km <= 9999 and 0 <= n_km <= 9999) or int(e_km) != e_km or int(n_km) != n_km:
        raise ValueError(f"cell corner out of range: {e_km}, {n_km}")
    return f"E{int(e_km):04d}N{int(n_km):04d}"


def parse_cell(value: str) -> tuple[int, int]:
    """`E0548N4330` -> (548, 4330), the lower-left corner in km."""
    m = _match(_CELL, value, "cell_id")
    return int(m.group(1)), int(m.group(2))


def patch_id(cell: str, kind: str, n: int) -> str:
    parse_cell(cell)
    if kind not in PATCH_KINDS:
        raise ValueError(f"unknown patch kind {kind!r}")
    if not 0 <= n <= 99999:
        raise ValueError(f"patch number out of range: {n}")
    return f"{cell}.{PATCH_KINDS[kind]}{n:05d}"


def parse_patch(value: str) -> tuple[str, str, int]:
    """-> (cell_id, kind, n)."""
    m = _match(_PATCH, value, "patch_id")
    if m.group(2) not in _KIND_OF_CODE:
        raise ValueError(f"unknown patch kind code in {value!r}")
    return m.group(1), _KIND_OF_CODE[m.group(2)], int(m.group(3))


# scopes ---------------------------------------------------------------------------------------------------------

STATEWIDE = "statewide"


def scope(region: str | None) -> str:
    """`region:<name>` for a region, `statewide` for None."""
    if region is None:
        return STATEWIDE
    _match(_REGION, region, "region name")
    return f"region:{region}"


def parse_scope(value: str) -> str | None:
    """-> region name, or None for statewide."""
    if value == STATEWIDE:
        return None
    if not isinstance(value, str) or not value.startswith("region:"):
        raise ValueError(f"not a scope: {value!r}")
    return _match(_REGION, value[7:], "region name").group(0)


def check_region(value: str) -> str:
    return _match(_REGION, value, "region name").group(0)
