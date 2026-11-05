"""data_src: catalogs, downloads, datum decisions, and the one reader of raw points (docs/design.md section 6).

Owns:   legacy_files, legacy_headers, deliveries, modern_batches, datum
Reads:  OT IN_2011_2013 (tile index + bucket), USGS EPT boundaries and data, Census counties, OSM, NAIP, CDL
Public: ilcd.data_src.points.read_legacy / read_modern (the only cross-module point reader)

Planned files (not implemented yet):
  catalog.py    legacy_files from the OT tile index + bucket listing; deliveries; modern_batches
  regions.py    region work area (county or polygon + buffer) -> file_ids and cell_ids
  fetch.py      download legacy files and EPT crops per cell into the cache; legacy_headers
  datum.py      per-delivery datum test (two hypotheses, n_samples cells) -> datum.json
  points.py     read_legacy / read_modern: project CRS, adjusted time, copy de-duplication, file/pass ids
  ancillary.py  OSM roads/buildings/water, NAIP NDVI, CDL, orthophotos per cell
"""
