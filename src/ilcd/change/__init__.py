"""change: maps of change between the corrected 2011 data and 3DEP.

Owns:   dod (per cell), m3c2 and objects (per site)
Reads:  points (data_src.points) corrected with align.apply, status (for the systematic LoD term)

DoD on the 2 m cell grid with a per-pixel LoD; M3C2 at 1 m core points (runs in envs/m3c2.yml); objects by DBSCAN
of significant same-sign change; structures (new/removed/rebuilt) handled separately.

Planned files: dod.py, m3c2.py, objects.py, structures.py.
"""
