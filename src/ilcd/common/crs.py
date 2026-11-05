"""Project coordinate reference system (docs/design.md section 2).

Points are converted to the project CRS only by ilcd.data_src.points, which applies each delivery's datum decision.
The helpers here are for vectors and bounding boxes (site polygons, catalog footprints, county boundaries), where
PROJ's default WGS84 -> NAD83(2011) path (metre-level) is good enough. Never use them for lidar points.
"""

from functools import lru_cache

from pyproj import CRS, Transformer

PROJECT_EPSG = 6345  # NAD83(2011) / UTM zone 16N
VERTICAL_EPSG = 5703  # NAVD88 height, realised with GEOID12B
PROJECT_CRS = f"EPSG:{PROJECT_EPSG}"
PROJECT_COMPOUND = f"EPSG:{PROJECT_EPSG}+{VERTICAL_EPSG}"
GEOGRAPHIC = "EPSG:4326"
US_SURVEY_FOOT_M = 1200 / 3937


@lru_cache(maxsize=32)
def transformer(src: str, dst: str = PROJECT_CRS) -> Transformer:
    """Cached always_xy transformer between two CRSs given as strings (EPSG codes, WKT or PROJ JSON)."""
    return Transformer.from_crs(CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True)


def to_project(x, y, src: str = GEOGRAPHIC):
    """Vectors only: (lon, lat) or src-CRS coordinates -> EPSG:6345 metres."""
    return transformer(src, PROJECT_CRS).transform(x, y)


def to_lonlat(x, y):
    """Vectors only: EPSG:6345 metres -> (lon, lat)."""
    return transformer(PROJECT_CRS, GEOGRAPHIC).transform(x, y)


def is_project(crs) -> bool:
    """True when crs (anything pyproj accepts) is EPSG:6345, alone or as the horizontal part of a compound CRS."""
    c = CRS.from_user_input(crs)
    if c.is_compound:
        c = c.sub_crs_list[0]
    return c.to_epsg() == PROJECT_EPSG
