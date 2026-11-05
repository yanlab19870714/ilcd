# ILCD: Indiana Lidar Correction and Change Detection

ILCD makes Indiana's first statewide airborne lidar (2011–13) comparable with the USGS 3DEP lidar flown in 2016–20, then maps what changed on the ground between the two surveys: riverbank erosion and deposition, new and removed buildings, and other earth-surface change.

The 2011–13 data carry flight-line errors of 5–15 cm, about the same size as much of the real change. ILCD estimates and removes those errors before differencing.

> **Status: early development.** The shared core (`ilcd.common`), all file contracts and the configuration are in place. The processing modules are being moved in one at a time, following the steps in [docs/modules.md](docs/modules.md). The pipeline does not run end to end yet.

## What it does

1. **Fetch data on demand.** ILCD downloads the 2011–13 tiles and cuts matching 3DEP points from the USGS Entwine (EPT) service. Everything is converted once into NAD83(2011) / UTM 16N (EPSG:6345), in metres, with NAVD88 (GEOID12B) heights. ILCD tests which datum each county's 2011 data is really in, because some files are labelled WGS84 but are actually NAD83.
2. **Rebuild the flight lines.** The 2011 tiles do not reliably record which flight line each point came from. ILCD reconstructs the lines from GPS time, heading and speed.
3. **Find stable reference surfaces.** These are roads, roofs and flat natural ground that are unlikely to have changed. ILCD measures the 2011 − 3DEP height difference on each surface for every flight line that covers it.
4. **Correct the 2011 data.** A least-squares model is fitted in two stages:
   - **Stage 1:** a vertical offset for each flight line, one along-track trend and one scan-angle curve for each acquisition block, and a horizontal shift for each line.
   - **Stage 2:** a smooth residual field for each line.

   The fitted model is saved as a small `model.json` that can be applied to any 2011 point.
5. **Evaluate the correction.** Accuracy is checked with:
   - held-out cross-validation;
   - consistency between overlapping lines;
   - synthetic change injected into the data, to test that real change is not absorbed by the model.

   Each line in each tile is then flagged as corrected, stage-1 only, or not corrected.
6. **Detect change.** ILCD maps change in two ways:
   - DEMs of difference on a 2 m grid, with a per-pixel level of detection;
   - M3C2 point-cloud distances.

   Significant change is grouped into objects, each with area, volume and depth.
7. **Compare and label.** ILCD runs the same evaluation on baseline methods: rigid shifts, ICP, strip adjustment, and C2C, C2M and learned change detectors. The tools produce 3D before/after views and map pages for labelling, with export to QGIS.

## Design

Each step is a separate module under `src/ilcd/`:

| Module | Role |
|---|---|
| `data_src` | Data catalogues, downloads and datum checks |
| `flightlines` | Rebuilds the 2011 flight lines |
| `refs` | Finds stable reference surfaces and measures height differences |
| `align` | Fits and applies the correction model |
| `eval` | Scores corrections and change maps |
| `change` | Maps change between the surveys |
| `baselines` | Runs the comparison methods |
| `viz_label` | Builds viewing and labelling pages |

Modules communicate only through versioned, validated files: Parquet, GeoParquet, GeoTIFF/COG, GeoJSON and JSON. Their contracts are in `src/ilcd/schemas/`. A Snakemake workflow chains the modules and submits the jobs to Slurm.

Work is organised by the natural unit of each step:

- **per legacy file**: flight passes;
- **per 1 km cell**: reference surfaces, observations and rasters;
- **per scope** (a region or the whole state): flight lines, the correction model and its evaluation;
- **per site**: change objects and labels.

This way, overlapping regions share results instead of recomputing them.

Point clouds and rasters are never stored in this repository. They live in a data store set by `ILCD_DATA_ROOT`.

Documentation:

- [docs/design.md](docs/design.md): the decisions everything relies on.
- [docs/modules.md](docs/modules.md): the code map and the refactor steps.
- [docs/contracts.md](docs/contracts.md): every file format.
- [docs/runbook.md](docs/runbook.md): setup and running.
- [docs/history.md](docs/history.md): what the exploratory phase found.

## Data sources

- Indiana statewide lidar 2011–13, downloaded from OpenTopography.
- USGS 3DEP lidar 2016–20, read through the public Entwine Point Tile (EPT) service.
- Ancillary layers: OpenStreetMap, NAIP imagery and USDA Cropland Data Layer.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
