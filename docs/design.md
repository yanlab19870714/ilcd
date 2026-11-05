# ILCD design

This document records the decisions that every module relies on. Change a decision here first, then the schemas in
`src/ilcd/schemas/`, then the code. Each section says why the decision was taken; most come from problems found while
exploring Monroe, Carroll and the East Fork box in 2026-10 (see `history.md`).

## 1. Goal and scope

ILCD corrects the 2011–13 Indiana statewide lidar (OpenTopography `IN_2011_2013`) so that it agrees with the
2016–20 USGS 3DEP lidar, and then maps surface change between the two. The tool must scale to the whole state
(44,675 legacy files, 0.51 TB; 3DEP read on demand from Entwine). It must also give the same answer for a location
whichever region it was requested through.

Out of scope for v1: correcting 3DEP itself, apart from one vertical offset per 3DEP batch; and change between
3DEP batches.

## 2. Conventions

| Item | Convention | Why |
|---|---|---|
| Horizontal CRS | NAD83(2011) / UTM zone 16N, **EPSG:6345**, metres | All of Indiana lies in UTM 16; avoids the two State Plane zones and US survey feet. |
| Vertical | NAVD88 orthometric height, **GEOID12B**, metres (compound EPSG:6345+5703) | 3DEP is delivered in NAVD88/GEOID12B. The 2011 data uses GEOID09, about 1.6 cm different, so it is converted. |
| Time | GPS time, **adjusted standard** (GPS seconds − 10⁹), float64 | LAS 1.4 standard. Week-seconds files are converted when their GPS week is known (section 5). |
| Units | metres, degrees, seconds; column names carry a unit suffix where ambiguous (`_m`, `_deg`, `_s`, `_m2`) | No hidden feet. |
| Angles | heading clockwise from grid north, 0–360°; scan angle signed as stored in LAS | |

Coordinates are converted **once**, when points are read, by `ilcd.data_src.points` (section 6). No other module
handles source CRSs, feet, datums or geoids.

## 3. Spatial and temporal units

| Unit | What it is | Identified by | Who defines it |
|---|---|---|---|
| **file** | One legacy LAZ as published by OT. The same file can be listed in several county folders. | `file_id` | catalog (data_src) |
| **delivery** | One OT folder `<zone>/<name>`. 50 are named after a county (`West_IN/Monroe`); two are multi-county batches (`East_IN/IN_E_2012`, `West_IN/IN_W_2013`), so 52 folders cover 92 counties. Datum, time encoding and point_source_id conventions are assumed constant within a delivery, and this is checked (section 6.4); they differ between deliveries. | `delivery_id` | catalog |
| **pass** | The points of one file recorded in one continuous stretch of GPS time, split at gaps > 5 s. It is the atomic unit of every legacy error term. | `pass_id` | flightlines |
| **line** | Passes from different files that belong to one straight flight line. | `line_id` (within a scope) | flightlines |
| **block** | Lines flown as one acquisition: overlapping in time and space, sharing scan-angle and trend terms. | `block_id` (within a scope) | flightlines |
| **cell** | A 1 km × 1 km square of the EPSG:6345 grid. Unit of point caching, reference-surface extraction, rasters and products. | `cell_id` | common.grid |
| **region** | A named work area from `config/regions/<name>.yaml`: a county or polygon plus a buffer. It is a selection of files and cells, used to run and check the pipeline. | region name | config |
| **scope** | The set of passes linked and solved together: one region, or `statewide`. | `region:<name>` or `statewide` | config |
| **site** | A polygon where change is mapped, viewed and labelled (e.g. Doug's East Fork box). It never affects the correction. | site name | `config/sites/<name>.geojson` |

### Why the units are split this way

- **County folders are not acquisitions.** At the Monroe/Brown line, both deliveries extend about 500 m past the
  county line. The two flights do not share a single point or GPS time (2026-11-05), so the overlap is a true
  block-to-block overlap and gives tie observations. Some 2012 deliveries (Adams/Jay/Wells, Allen/Huntington) instead
  copy whole tiles into each county folder: 2,152 file names occur in more than one folder, and in 517 of them the
  copies differ in size but hold identical points. A pipeline keyed on county folders would therefore miss tie points
  in one place and count points three times in another.
- **Per-location work is done per cell, not per region.** Patches, observations and rasters are computed once per
  cell and shared by every region that covers the cell. Regions that overlap (through their buffers) can then never
  disagree, or double-count, on the same ground.
- **Per-file work is done per file.** A pass is a property of a file and does not depend on the region asking.
- **Observations refer to passes, not lines.** Linking can be redone in any scope (one region, or statewide) without
  measuring again. `line_id`s are only meaningful inside their scope.

## 4. Identifiers

All identifiers are strings, deterministic, and parseable by `ilcd.common.ids`.

| ID | Format | Example |
|---|---|---|
| `delivery_id` | `<E\|W>_<folder name>` | `W_Monroe`, `E_IN_E_2012`, `E_St_Joseph` |
| `file_id` | `<E\|W>.<file stem>` | `E.ot_in2012_04152060_12` |
| `pass_id` | `<file_id>#<k>`, k = 1.. in time order within the file | `W.ot_in2011_31501470_12_1#2` |
| `cell_id` | `E<easting km, 4 digits>N<northing km, 4 digits>` (lower-left corner) | `E0548N4330` |
| `patch_id` | `<cell_id>.<kind code><n, 5 digits>` | `E0548N4330.R00017` |
| `time_domain` | `abs` for adjusted-standard time; `wk:<delivery_id>` for week-seconds deliveries whose week is unknown | `wk:W_Monroe` |
| `line_id` | `<time_domain>@<start time of its first pass, 0.1 s>` | `abs@-15390699.4` |
| `block_id` | `<time_domain>@<start time of its first line, 1 s>#B` | `abs@-15390699#B` |

File names never occur in both zones (checked 2026-11-05), so `zone + stem` is unique. Tile codes alone are not
unique: 2,816 codes occur in more than one folder, and the East and West grids reuse numbers.

## 5. Time

- data_src reads each file's LAS global-encoding bit and its GPS-time range. It records `gps_time_type`
  (`adjusted_standard` or `week_seconds`) per file, and checks that the bit agrees with the value range. A disagreement
  is an error.
- Adjusted-standard files are used as they are. Their passes have `time_domain = abs` and can be linked across
  deliveries.
- Week-seconds files (e.g. Monroe) need a GPS week. The week is set per delivery in `config/deliveries.yaml` when it is
  known from metadata. Otherwise the delivery's passes get `time_domain = wk:<delivery_id>`, are linked only within
  that delivery, and never form lines with other deliveries. They are still corrected; only cross-delivery line
  joining is lost.
- Risk within a week-seconds delivery: passes from different weeks with similar week seconds. They would also need
  matching heading, along-track distance and cross-track position to be wrongly linked. The flightlines QA reports
  any line whose passes span more than one calendar day when dates are known.

## 6. Data access

### 6.1 Catalogs (statewide, built once)

- `legacy_files`: one row per **distinct file** (`file_id`), built from OT's `IN_2011_2013_TileIndex.shp` plus the
  bucket listing. It records the footprint in EPSG:6345, the canonical URL (first folder alphabetically), and every
  folder that lists the file.
- `legacy_headers`: filled when a file is first fetched. It records the sha256, point count, GPS range and time
  type, scale and offset, and whether the copies in other folders contain the same points.
- `deliveries`: one row per OT folder. It records the time type, GPS week if known, datum decision and point_source_id
  meaning.
- `modern_batches`: one row per 3DEP EPT resource covering Indiana, with its boundary.

### 6.2 Selecting data by geometry

A region's footprint is a polygon: a county from the US Census cartographic boundary file, or a polygon file. The
region work area is the footprint plus `data_src.region_buffer_m` (default 1,000 m). The region uses:

- every `legacy_files` row whose footprint intersects the work area, whatever folder it is in;
- every cell intersecting the work area;
- for each cell, every 3DEP batch whose boundary intersects it.

### 6.3 Reading points: `ilcd.data_src.points`

This is the **only** reader of raw point clouds. It is one of two cross-module function calls allowed (section 8).

- `read_legacy(cell_id | bounds, classes=..., fields=...)` returns 2011 points in EPSG:6345 + NAVD88/GEOID12B,
  adjusted-standard time where known, with `file_id` and `pass_id` attached.
  - Points from copies of the same file are de-duplicated on (GPS time, X, Y, Z).
  - The datum transform comes from the delivery's `datum.json`. A delivery with no decided datum is refused.
- `read_modern(cell_id | bounds, ...)` returns 3DEP points with the batch id attached. They are fetched from EPT once
  per cell and cached as LAZ.

### 6.4 Datum decision per delivery

`datum.json` holds a test of both hypotheses for each delivery:

- `itrf`: the WGS84 label is real; transform ITRF2014 → NAD83(2011) at epoch 2011.5.
- `nad83`: the label is wrong and the data is already NAD83.

The test runs at `data_src.datum.n_samples` (default 3) sample areas spread across the delivery's footprint, which
matters for the two multi-county batch folders. Each sample uses roofs if there are enough, otherwise ground. For
each hypothesis it records:

- the residual horizontal shift that best fits 3DEP;
- the NMAD before and after the shift;
- the point count.

A hypothesis passes a sample when its residual shift is below `data_src.datum.max_residual_m` (0.3 m). The delivery's
decision is the hypothesis that passes **every** sample while the other fails at least one. Any other outcome is
`ambiguous`: data_src stops with an error, and the delivery needs a manual entry in `config/deliveries.yaml`. It is
also an error when one file, listed in two deliveries, gets a different decision from each.

## 7. Pipeline and stores

```
statewide catalogs ─► files/<file_id>/passes ─► scope: lines, blocks ─┐
                       cells/<cell_id>/refs: patches, obs, overlap_obs ┴► scope: align/model ─► scope: eval
                       cells/<cell_id>/change: dod  ◄── model ──► sites/<site>: m3c2, objects, viz, labels
```

| Store | Path under `$ILCD_DATA_ROOT` | Written by |
|---|---|---|
| raw cache | `cache/legacy/<OT path>`, `cache/modern/<batch>/<cell_id>.laz`, `cache/ancillary/<source>/` | data_src |
| statewide catalogs | `statewide/catalog/*.parquet`, `statewide/deliveries/<delivery_id>/datum.json` | data_src |
| per file | `files/<file_id>/passes.parquet` | flightlines |
| per cell | `cells/<cell_id>/refs/*.parquet`, `cells/<cell_id>/change/dod.tif` | refs, change |
| per scope | `regions/<name>/…` or `statewide/scope/…`, with `flightlines/`, `align/model/`, `eval/`, `baselines/<method>/` | flightlines, align, eval, baselines |
| per site | `sites/<site>/change/`, `sites/<site>/viz/`, `sites/<site>/labels/labels.geojson` | change, viz_label |

All paths come from `ilcd.common.paths`; no module builds a data path by hand.

**Linking and solving.**

- Each region scope links its own passes and solves its own model. This is the per-county check: datum, line purity,
  CV.
- The statewide scope links all available passes and solves one sparse least-squares problem over all observations
  and overlap observations. It is re-run whenever regions are added. Its observations total about 10⁵–10⁶ rows.
- The statewide model is the product. A region model is a diagnostic.

**Empty is not an error.** A file with no usable pass, or a cell with no patches (forest, lake), writes a valid empty
output with `status: empty` in its manifest. Errors are for real problems: schema violations, datum ambiguity,
time-encoding mismatch, missing inputs.

## 8. Modules and the file-contract rule

Modules communicate through files that follow a versioned contract in `src/ilcd/schemas/`. Each contract has one
owner module. Writers validate before writing, and readers validate on reading (`ilcd.common.io`). A module never
imports another business module. The exceptions are:

1. `ilcd.common.*`, which any module may use;
2. `ilcd.data_src.points` (read raw points: section 6.3);
3. `ilcd.align.apply` (apply a correction model to 2011 points).

Points are too large to pass through intermediate files, and the model must be applied identically everywhere. Each
of these exceptions is a single function with a stable signature.

| Module | Owns contracts | Reads |
|---|---|---|
| data_src | `legacy_files`, `legacy_headers`, `deliveries`, `modern_batches`, `datum` | OT, EPT, Census, OSM, NAIP, CDL |
| flightlines | `passes`, `lines`, `line_members`, `blocks` | points |
| refs | `patches`, `obs`, `overlap_obs` | points, ancillary |
| align | `model` (directory: `model.json`, `members.parquet`, `stage2.npz`) | `obs`, `overlap_obs`, `lines`, `line_members`, `blocks` |
| eval | `cv`, `status`, `injection`, `overlap_check`, `change_eval` | everything above, `labels` |
| change | `dod`, `m3c2`, `objects` | points + model |
| baselines | the same contracts as align/change, under `baselines/<method>/` | same as align/change |
| viz_label | `labels`, web pages, QGIS projects | `objects`, points, imagery |

Formats:

- tables: Parquet;
- machine-written vectors: GeoParquet;
- human-written vectors (`sites`, `labels`): GeoJSON, always EPSG:4326 as RFC 7946 requires, converted on reading;
- rasters: Cloud-Optimised GeoTIFF;
- documents: JSON;
- point clouds: LAZ, with COPC for products.

GeoPackage is only read, as an external input.

## 9. Configuration

- `src/ilcd/config/defaults.yaml` holds every tunable parameter, grouped by module, with its unit. It ships with the
  package because the code depends on it.
- `config/regions/<name>.yaml` and `config/sites/<name>.geojson` are user-facing.
  - Region files may override parameters only under `params:`.
  - An override of a key that is not in the defaults is an error.
- `config/deliveries.yaml` holds manual per-delivery facts: GPS week, datum override, point_source_id meaning. Every
  entry names its evidence.
- Machine-specific settings never enter the repository:
  - `ILCD_DATA_ROOT`: the data store;
  - `ILCD_ENV_ROOT`: the conda environments;
  - `ILCD_SLURM_ACCOUNT`;
  - a Snakemake profile copied from `config/profiles/example/`.

## 10. Provenance

Every step writes `<step>.manifest.json` next to its outputs. It records:

- the step name and status (`ok` or `empty`);
- the code version (git commit, plus a dirty flag);
- the parameter values used and their hash;
- the inputs (path plus sha256 for contract files; path plus catalog sha256 for raw files);
- the outputs with their sha256;
- the start and end time, host and Slurm job id.

Snakemake decides what to re-run. The manifest records what was actually done.

## 11. Testing and acceptance

- **Unit tests** (`tests/`, no data needed): common utilities, schema files, configs. Run `pytest`.
- **Data tests** (marker `data`, need `ILCD_DATA_ROOT`): module runs on a small fixture area. The fixture is three
  cells at the Monroe/Brown county line, around file `W.ot_in2011_31501470_12_1`. It covers a clipped half-tile, two
  deliveries with different time encodings, overlapping flights and a forested cell with no hard ground.
- **Acceptance per refactor step**:
  - **Carroll**: lines match point_source_id with purity 1.00 both ways (31 lines). Stage-1 CV RMS matches a rerun
    of the old code on the same 31 lines, to within 1 mm.
  - **Monroe**: same CV checks. The 11 forest and edge tiles that crashed the old code produce passes and, where
    natural ground exists, observations.
  - **Bartholomew + Jackson**: first cross-county scope. The East Fork site reproduces or explains the 325 change
    objects.

## 12. Open questions

- GPS week of week-seconds deliveries (Monroe): look for acquisition dates in OT/IndianaMap metadata.
- How many deliveries were flown jointly across county lines: check one boundary tile pair per county line once the
  catalog exists.
- point_source_id meaning per delivery (a line in Carroll, a sub-tile in Monroe): record it in `deliveries`, but
  never rely on it for linking.
