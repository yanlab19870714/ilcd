"""The 1 km cell grid in EPSG:6345 and the pixel grids aligned to it (docs/design.md section 3).

A cell [e, e + 1 km) x [n, n + 1 km) is named by its lower-left corner. Rasters of a cell are north-up with their
top-left corner at (e, n + 1 km), so pixels of every resolution that divides 1 km line up across cells.
"""

import math

import numpy as np

from . import ids

CELL_M = 1000


def cell_index(x, y):
    """Integer cell indices (km) of points; works on scalars and arrays."""
    return np.floor_divide(np.asarray(x, dtype=float), CELL_M).astype(np.int64), np.floor_divide(
        np.asarray(y, dtype=float), CELL_M
    ).astype(np.int64)


def cell_of(x: float, y: float) -> str:
    """Cell id containing one point."""
    i, j = cell_index(x, y)
    return ids.cell_id(int(i), int(j))


def cell_bounds(cell: str) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) in metres."""
    e, n = ids.parse_cell(cell)
    return e * CELL_M, n * CELL_M, (e + 1) * CELL_M, (n + 1) * CELL_M


def cells_in_bounds(xmin: float, ymin: float, xmax: float, ymax: float) -> list[str]:
    """Cells whose interior intersects the box (cells that only touch an edge are left out)."""
    if not (xmax > xmin and ymax > ymin):
        raise ValueError(f"empty box {xmin, ymin, xmax, ymax}")
    i0, j0 = math.floor(xmin / CELL_M), math.floor(ymin / CELL_M)
    i1, j1 = math.ceil(xmax / CELL_M), math.ceil(ymax / CELL_M)
    return [ids.cell_id(i, j) for j in range(j0, j1) for i in range(i0, i1)]


def cells_for_geometry(geom) -> list[str]:
    """Cells sharing area with a shapely (multi)polygon in EPSG:6345."""
    from shapely.geometry import box

    if geom.is_empty:
        return []
    return [c for c in cells_in_bounds(*geom.bounds) if geom.intersection(box(*cell_bounds(c))).area > 0]


def pixel_grid(cell: str, res_m: float) -> tuple[tuple[float, float, float, float, float, float], int, int]:
    """(affine transform as (a, b, c, d, e, f), width, height) of a north-up raster of one cell at res_m."""
    n = CELL_M / res_m
    if abs(n - round(n)) > 1e-9:
        raise ValueError(f"resolution {res_m} m does not divide the {CELL_M} m cell")
    xmin, _, _, ymax = cell_bounds(cell)
    return (res_m, 0.0, xmin, 0.0, -res_m, ymax), int(round(n)), int(round(n))
