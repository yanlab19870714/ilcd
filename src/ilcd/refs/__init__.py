"""refs: stable reference surfaces per cell and their observations by each legacy pass.

Owns:   patches, obs, overlap_obs
Reads:  points (data_src.points), ancillary layers, passes

Surfaces: roads and other hard ground (OSM + NDVI), roofs (plane growing), flat natural ground (also under canopy,
new), sloped natural ground (horizontal-shift term). Screening: water buffer, cropland (CDL, new), roughness, and
cell medians far from their neighbours (real change such as quarries). Excluded patches are kept with a reason.
A cell with no surfaces writes empty outputs (status empty), never an error.

Planned files: hard_ground.py, roofs.py, natural.py, screen.py, observe.py, overlap.py, segment_dl.py (later).
"""
