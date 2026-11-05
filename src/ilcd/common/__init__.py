"""Shared utilities every module may use (docs/design.md section 8).

ids        identifier constructors and parsers
crs        project CRS constants; vector-only transforms
grid       the 1 km cell grid and aligned pixel grids
gpstime    GPS week / adjusted standard time conversions and checks
paths      every location in the data store and config directory
contracts  contract specs (ilcd/schemas) and validation
io         validated, atomic reading and writing of contract files
config     defaults, region/site/delivery configuration, parameter overrides
manifest   provenance manifests of step runs
log        logging
"""
