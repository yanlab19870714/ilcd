import numpy as np
import pytest
from shapely.geometry import Polygon, box

from ilcd.common import grid


def test_cell_of_and_bounds():
    assert grid.cell_of(548123.4, 4330999.9) == "E0548N4330"
    assert grid.cell_bounds("E0548N4330") == (548000, 4330000, 549000, 4331000)
    i, j = grid.cell_index(np.array([548000.0, 548999.999]), np.array([4330000.0, 4330000.5]))
    assert list(i) == [548, 548] and list(j) == [4330, 4330]


def test_cells_in_bounds_excludes_touching():
    assert grid.cells_in_bounds(548000, 4330000, 549000, 4331000) == ["E0548N4330"]
    assert grid.cells_in_bounds(548500, 4330500, 549500, 4330600) == ["E0548N4330", "E0549N4330"]
    with pytest.raises(ValueError):
        grid.cells_in_bounds(1, 1, 1, 2)


def test_cells_for_geometry():
    tri = Polygon([(548100, 4330100), (550900, 4330100), (548100, 4332900)])
    cells = grid.cells_for_geometry(tri)
    assert "E0548N4330" in cells and "E0550N4332" not in cells
    assert grid.cells_for_geometry(box(548000, 4330000, 549000, 4331000)) == ["E0548N4330"]


def test_pixel_grid():
    t, w, h = grid.pixel_grid("E0548N4330", 2.0)
    assert (w, h) == (500, 500)
    assert t == (2.0, 0.0, 548000, 0.0, -2.0, 4331000)
    with pytest.raises(ValueError):
        grid.pixel_grid("E0548N4330", 3.0)
