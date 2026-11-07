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

**The horizontal frame is 3DEP's.** Indiana 3DEP was delivered in NAD83(HARN) State Plane, and the public EPT holds
it in EPSG:3857 with a null datum shift. ilcd undoes that projection with the same null shift: EPSG:3857 → lon/lat
taken as NAD83 → EPSG:6345. A 2011 delivery decided `nad83` is projected the same way. A delivery decided `itrf` is
first transformed ITRF2014 → NAD83(2011) at epoch 2011.5. This matches the exploratory code, which compared EPT with
the original 3DEP files to 0.6 cm. The cm-level HARN–2011 realisation difference is not modelled; it is common to
both epochs or absorbed by the per-line shifts. Heights: 3DEP is metres, NAVD88/GEOID12B. 2011 is US survey feet,
NAVD88/GEOID09, and is converted with the NOAA grids named in `data_src.geoid`.

## 3. Spatial and temporal units

| Unit | What it is | Identified by | Who defines it |
|---|---|---|---|
| **file** | One legacy LAZ as published by OT. The same file can be listed in several county folders. | `file_id` | catalog (data_src) |
| **delivery** | The innermost OT folder that holds LAZ files. There are three shapes: 50 county folders (`West_IN/Monroe`); the flat batch `East_IN/IN_E_2012`; and the batch `West_IN/IN_W_2013`, which has 30 county subfolders (`IN_W_2013/Dubois`). Together they cover all 92 counties. The top-level folder is kept as `batch`. Datum, time encoding and point_source_id conventions are assumed constant within a delivery, which is checked (section 6.4), and differ between deliveries. | `delivery_id` | catalog |
| **pass** | The points of one file recorded in one continuous stretch of GPS time, split at gaps > 5 s. It is the atomic unit of every legacy error term. | `pass_id` | flightlines |
| **line** | Passes from different files that belong to one straight flight line. | `line_id` (within a scope) | flightlines |
| **block** | Lines of one flying season: a time domain split where lines are more than `flightlines.block.max_gap_s` (30 days) apart. A block shares one scan-angle curve and one along-track trend. Carroll 2011 is one block of five sorties over 18 days, which is the setting verified in the exploration. | `block_id` (within a scope) | flightlines |
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
- **Per-location work is done per cell, not per region.** Patches, observations and their rasters are computed once
  per cell and shared by every region that covers the cell. Regions that overlap (through their buffers) can then
  never disagree, or double-count, on the same ground. Work that uses a model (change) is still done per cell, but
  stored under the model's scope (section 7.1).
- **Per-file work is done per file.** A pass is a property of a file and does not depend on the region asking.
- **Observations refer to passes, not lines.** Linking can be redone in any scope (one region, or statewide) without
  measuring again. `line_id`s are only meaningful inside their scope.

## 4. Identifiers

All identifiers are strings, deterministic, and parseable by `ilcd.common.ids`.

| ID | Format | Example |
|---|---|---|
| `delivery_id` | `<E\|W>_<folder>`, with `/` in nested folders written as `-` | `W_Monroe`, `E_IN_E_2012`, `W_IN_W_2013-Dubois` |
| `file_id` | `<E\|W>.<file stem>` | `E.ot_in2012_04152060_12` |
| `pass_id` | `<file_id>#<k>`, k = 1.. in time order within the file | `W.ot_in2011_31501470_12_1#2` |
| `cell_id` | `E<easting km, 4 digits>N<northing km, 4 digits>` (lower-left corner) | `E0548N4330` |
| `patch_id` | `<cell_id>.<kind code><n, 5 digits>` | `E0548N4330.R00017` |
| `time_domain` | `abs` for adjusted-standard time; `wk:<delivery_id>` for week-seconds deliveries whose week is unknown | `wk:W_Monroe` |
| `line_id` | `<time_domain>@<start time of its first pass, 0.1 s>`, plus `#k` (k = 2, 3, …, in pass_id order) when several lines of a scope start in the same 0.1 s | `abs@-15390699.4`, `abs@50028600.3#2` |
| `block_id` | `<time_domain>@<start time of its first line, 1 s>#B` | `abs@-15390699#B` |

File names never occur in both zones (checked 2026-11-05), so `zone + stem` is unique. Tile codes alone are not
unique: 2,816 codes occur in more than one folder, and the East and West grids reuse numbers.

## 5. Time

- data_src reads each file's LAS global-encoding bit and its GPS-time range. It records `gps_time_type`
  (`adjusted_standard` or `week_seconds`) per file, and checks that the bit agrees with the value range. A disagreement
  is an error.
- A delivery whose headers all carry the wrong bit gets `gps_time_encoding` in `config/deliveries.yaml`, which replaces
  the bit for its files in every reader (data_src.fetch.adjusted_bit); the range check then applies to the corrected
  encoding. E_Hamilton (las2las export with the bit cleared, times in adjusted standard) is the known case.
- A download chunk fetches every file it can, then fails naming the files that did not pass these checks, so one
  rerun after a fix completes it.
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
  - The index footprints equal the files' header boxes (300 of 300 sampled, 2026-11-07). One file is missing from the
    index (W ot_in2013_30401150_12_1); its header box spans Kansas to Indiana because 147 of its 4.2 M points carry
    stray coordinates. A file missing from the index therefore gets the box of its points' 0.1-99.9 percentiles
    (`data_src.footprint_trim_pct`), and the legacy reader drops points more than `data_src.points.footprint_pad_m`
    outside their file's footprint box: a no-op for indexed files, it removes such stray points.
- `legacy_headers`: one row per file, written when the file is fetched. It records the sha256, point count, GPS range
  and time type, scale and offset, and whether the copies in other folders contain the same points.
- `deliveries`: one row per OT folder. It records the time type, GPS week if known, datum decision and point_source_id
  meaning. The time type is decided from the headers of all the delivery's files, never from part of them.

All four are written by the statewide catalog stage (`workflow/rules/statewide.smk`), which fetches every legacy file
and tests every delivery's datum before any region runs. Region runs only read them. Until 2026-11-07 region runs
rebuilt `legacy_headers` and `deliveries` from the files fetched so far: two regions could not run at once, and a
delivery's time type depended on which of its files had been fetched (15 of the 19 deliveries touched then were
partly fetched).
- `modern_batches`: one row per **reference** 3DEP batch, with its boundary. The reference is the 2016–20 statewide
  program: 13 EPT resources `IN_Statewide_*_2017`, selected by `data_src.ept.reference_batches`. The public EPT
  bucket also holds the 2011–13 legacy data itself (`IN_Statewide-<County>Co_2011`), 2010–13 county projects and
  neighbouring states. 144 resources intersect Indiana (checked 2026-11-05), and none of the others may serve as
  reference.

### 6.2 Selecting data by geometry

A region's footprint is a polygon: a county from the US Census cartographic boundary file, or a polygon file. The
region work area is the footprint plus `data_src.region_buffer_m` (default 1,000 m). The region uses:

- every `legacy_files` row whose footprint intersects the work area, whatever folder it is in;
- every cell intersecting the work area;
- for each cell, every reference batch whose boundary intersects it. Where batches overlap at their seams, the
  per-cell steps use the one batch with the most ground points in the cell (its *primary batch*) and record it on
  every patch. The model's per-batch vertical offsets tie batches together through the 2011 lines that cross the
  seams.

### 6.3 Reading points: `ilcd.data_src.points`

This is the **only** reader of raw point clouds. It is one of two cross-module function calls allowed (section 8).

- `read_legacy(cell_id | bounds, classes=..., fields=...)` returns 2011 points in EPSG:6345 + NAVD88/GEOID12B,
  adjusted-standard time where known, with `file_id` and `pass_id` attached.
  - Only the canonical copy of a file is read. Copies in other folders hold the same points; this was checked on
    Allen/Huntington `04152060`, and `ilcd data_src check-copies` samples more files statewide. If two copies ever
    differ in their points, they become separate `file_id`s, the copy's folder is appended to the stem, and this rule
    is revisited.
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

The test is **relative**. The two readings differ by a fixed transform, about 1.1 m in Indiana. One flight line's
own horizontal error can leave a few decimetres of residual shift even under the right reading. That happened at
W_IN_W_2013-Tippecanoe on 2026-11-05: 0.5 m in one sample against 1.5 m under the other reading. So:

- In each informative sample, the reading whose residual shift is smaller by at least `data_src.datum.min_margin_m`
  (0.4 m) wins.
- The decision is the reading that wins every informative sample, with at least `min_informative` (2) of them.
- Any other outcome is `ambiguous`: data_src stops with an error, and the delivery needs a manual entry in
  `config/deliveries.yaml`.
- A winner whose residual exceeds `max_residual_m` (0.3 m) is recorded as a diagnostic. It points to a line-level
  shift for align to estimate, not to a datum problem.

It is
also an error when one file, listed in two deliveries, gets a different decision from each.

## 7. Pipeline and stores

```
statewide catalogs ─► files/<file_id>/passes ─► scope: lines, blocks ─┐
                       cells/<cell_id>/refs: patches, obs, overlap_obs ┴► scope: align/model ─► scope: eval
                                       scope: change/dod ◄── model ──► sites/<site>: m3c2, objects, viz, labels
```

| Store | Path under `$ILCD_DATA_ROOT` | Written by |
|---|---|---|
| raw cache | `cache/legacy/<OT path>`, `cache/modern/<batch>/<cell_id>.laz`, `cache/ancillary/<source>/` | data_src |
| statewide catalogs | `statewide/catalog/*.parquet`, `statewide/deliveries/<delivery_id>/datum.json` | data_src |
| per file | `files/<file_id>/passes.parquet` | flightlines |
| per cell | `cells/<cell_id>/refs/*.parquet` | refs |
| per scope | `regions/<name>/…` or `statewide/scope/…`, with `flightlines/`, `align/model/`, `eval/`, `change/dod/`, `baselines/<method>/` | flightlines, align, eval, change, baselines |
| per site | `sites/<site>/change/` (m3c2 per cell, objects), `sites/<site>/viz/`, `sites/<site>/labels/labels.geojson` | change, viz_label |

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

### 7.1 Change detection (refactor step 4)

**Which model corrects a place.** A correction depends on the scope that fitted it. A site names its scope
(property `scope` of the site file; statewide when absent); eastfork_doug uses `region:bartholomew_jackson`. Change
products therefore live under the scope or the site, never under a bare cell:

| Product | Path | Unit of work |
|---|---|---|
| DoD | `{scope}/change/dod/<cell_id>/dod.tif` | cell |
| M3C2 | `sites/<site>/change/m3c2/<cell_id>/m3c2.parquet` | cell of the site |
| objects | `sites/<site>/change/objects.parquet` | site |

**Frames.** `align.method.apply` corrects 2011 points into the frame of the model's reference 3DEP batch
(vertical terms subtracted, horizontal shift moved back); `align.method.to_reference_batch` adds the fitted batch
offset to 3DEP heights of other batches. Points that no pass of the model covers keep a flag `corrected = False`;
change leaves them out (their pixels have no 2011 value).

**Level of detection.** The systematic part comes from `eval/status` (per cell and line, from held-out residuals):
`s_sys = lod95 / 1.96`, weighted by each line's points in the pixel or cylinder. Lines without a status row, or
`no_data`, use the median `lod95` of the scope's corrected rows.
- DoD pixel: `lod = 1.96 * sqrt(s_leg^2 / n_leg + s_mod^2 / n_mod + s_sys^2)`, n = the pixel's ground points,
  s = their standard deviation about the pixel means, pooled over `change.dod.sd_window_px` pixels square. A 2 m
  pixel holds only 3-4 2011 ground points; their own SD is unstable and made 7-13% of Monroe pixels "lowered".
- M3C2: py4dgeo's level of detection with `registration_error = s_sys` of the cell.

**DoD classes.** 2 m pixels with at least `change.dod.min_points` ground points per epoch. Stable, lowered or
raised by `|dz| > lod`. Land lost to water: 2011 ground and 3DEP water. Land gained: the reverse. Water is a class-9
return, or a 3DEP pixel with no ground within 4 m (history.md section 5). Masked: buildings in either epoch, or
water in both.

**What a DoD class means.** A class is a per-pixel test at 95%: on unchanged ground about 5% of pixels are
flagged by chance, and a local residual offset of the correction (the spread of `status.bias_m` is about 3 cm)
moves those false flags to one side. Monroe check (2026-11-06, 4 cells): roads with dz -1.4 cm and NMAD 3.8 cm had
7.5% of pixels "lowered" and 3% "raised", as a normal distribution predicts; pixel medians instead of means changed
nothing. The change product is therefore the object map, which needs area (`min_area_m2`) and magnitude
(`min_magnitude_m`), not the DoD classes.

**M3C2 and objects.** As in the exploration (history.md section 4): core points are 3DEP ground thinned to
`change.m3c2.core_res_m`, normals from 3DEP ground, cylinders of `cylinder_radius_m`. M3C2 runs per cell with
the cell padded by `max_distance_m`, keeping core points inside the cell. Objects: significant core points
(|d| > lod and |d| >= `change.objects.min_magnitude_m`), split by sign, clustered with DBSCAN over the whole site
so that objects cross cell borders. M3C2 uses py4dgeo 1.2.1, installed in the main environment (its pip dependencies leave numpy alone; the
exploration's separate environment is not needed).

## 8. Modules and the file-contract rule

Modules communicate through files that follow a versioned contract in `src/ilcd/schemas/`. Each contract has one
owner module. Writers validate before writing, and readers validate on reading (`ilcd.common.io`). A module never
imports another business module. The exceptions are:

1. `ilcd.common.*`, which any module may use;
2. the raw-data readers of data_src: `ilcd.data_src.points` (points: section 6.3) and `ilcd.data_src.ancillary`
   (per-cell ancillary layers such as NAIP NDVI, OSM and CDL);
3. the **correction-method interface**: `ilcd.align.method` and each correction baseline expose the same three
   functions:
   - `fit(design_inputs, params) -> Model`;
   - `Model.predict(obs)`, the correction at observation rows;
   - `apply(model, points)`, the correction of 2011 points.

   eval calls them to refit inside cross-validation folds. change and baselines call `apply` to correct points.

Points are too large to pass through intermediate files. The model must be evaluated identically everywhere, and
cross-validation must refit it. Each of these exceptions is a small set of functions with a stable signature.

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

Snakemake decides what to re-run for whole-scope steps. The manifest records what was actually done.

Per-file and per-cell steps run in chunks with `--skip-existing`, and Snakemake cannot see their units; the manifest
decides instead (`manifest.current`). A unit is skipped only when its manifest was made with the same params and none
of its hashed inputs has changed since (an input newer than the manifest is re-hashed). So a step's params and inputs
must cover what it depends on:

- `flightlines.passes`: flightlines params plus its delivery's time domain, GPS week and datum;
- `refs.cell`: refs params plus the decisions of every delivery it reads; inputs: the passes of those files;
- `change.dod`, `change.m3c2`: their params; inputs: the scope's model and status table.

A new model, a changed delivery decision or new passes therefore recompute exactly the affected units. Downloads into
the raw caches (`fetch_legacy`, `fetch_modern`) are skipped on any manifest: they depend on no parameter or upstream
output. Code changes are not detected; after one, run the step without `--skip-existing`. Until 2026-11-07 any
manifest meant done, and forcing a chunked rule in Snakemake re-ran nothing (found when the East Fork change products
were "re-run" on a new model).

## 11. Testing and acceptance

- **Unit tests** (`tests/`, no data needed): common utilities, schema files, configs. Run `pytest`.
- **Data tests** (marker `data`, need `ILCD_DATA_ROOT`): module runs on a small fixture area. The fixture is three
  cells at the Monroe/Brown county line, around file `W.ot_in2011_31501470_12_1`. It covers a clipped half-tile, two
  deliveries with different time encodings, overlapping flights and a forested cell with no hard ground.
- **Acceptance per refactor step** (results in `acceptance.md`):
  - **Carroll** (region `carroll_acceptance`, delivery W_Carroll only). Each check isolates one cause of
    difference from the exploration:
    1. *Port*: the exploration's own observations, fitted and cross-validated by ilcd (tile folds), must reproduce
       its numbers exactly.
    2. *Lines*: lines match point_source_id with purity 1.00 both ways (31 lines).
    3. *Datum*: W_Carroll is decided `nad83`.
    4. *Observations*: under the same metric (1 km cell folds and segments), ilcd's observations must give a
       stage-1 CV RMS no higher than the exploration's.

    Matching the exploration's absolute number is not a criterion. Its tiles (1.5 km) make larger segments
    than cells, and ilcd's observations differ on purpose: GEOID12B, one 3DEP batch per cell, screening across
    cells.
  - **Monroe**: the same checks. In addition, the 11 forest and edge tiles that crashed the old code must produce
    passes and, where natural ground exists, observations.
  - **Bartholomew + Jackson**: the first cross-county scope. The East Fork site reproduces or explains the 325
    change objects.

## 12. Open questions

- Blocks are flying seasons. Statewide, one along-track trend per season may be too coarse, because it was only
  verified per county. Test a per-sortie trend in refactor step 6.
- GPS week of week-seconds deliveries (Monroe): look for acquisition dates in OT/IndianaMap metadata.
- How many deliveries were flown jointly across county lines: check one boundary tile pair per county line once the
  catalog exists.
- point_source_id meaning per delivery (a line in Carroll, a sub-tile in Monroe): record it in `deliveries`, but
  never rely on it for linking.
