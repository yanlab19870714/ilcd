"""change: maps of change between the corrected 2011 data and 3DEP (docs/design.md section 7.1).

Owns:   dod (per cell, under the scope whose model corrected it), m3c2 (per cell of a site), objects and
        object_members (per site)
Reads:  points (data_src.points) corrected with align.method.apply, eval/status (systematic LoD term), OSM water

DoD on the 2 m cell grid with a per-pixel LoD; M3C2 at 1 m core points (py4dgeo); objects by DBSCAN of
significant same-sign change. Structures (new/removed/rebuilt, change.structures) are not implemented yet.
"""
