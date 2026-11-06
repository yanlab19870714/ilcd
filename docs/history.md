# What the exploration found (2026-10-20 to 2026-11-05)

ilcd rewrites about two weeks of exploratory scripts. This page keeps what those scripts learned, so that nobody
has to rediscover it. Each item is marked:

- **verified**: checked on data, with the evidence named;
- **observed**: seen once or in one area only.

The numbers come from runs under `/N/project/yanlab_space/indiana_lidar/runs/`. The old code is archived read-only
at `/N/project/yanlab_space/indiana_lidar/legacy_code/indiana-lidar_2026-11-05.tar.gz`, with its sha256 beside it.

## 1. The 2011–13 data (OpenTopography `IN_2011_2013`)

### 1.1 Datum

- **The datum differs between deliveries.**
  - Monroe: the EPSG:4326 label is real. Converting with ITRF2014 → NAD83(2011) at epoch 2011.5 is required. PROJ's
    default 4326 → NAD83 path is a null transform and leaves E +0.70, N −0.83 m. *Verified* on tile 31101425 and
    county-wide.
  - Carroll: the data is already NAD83. Applying the ITRF shift leaves E +0.6, N −0.8 m on 58 of 59 roof-solved
    tiles. *Verified*.
  - Hence the datum is tested per delivery (design.md section 6.4).
- **Vertical offset.** Even after the datum fix, 2011 ground lies about 7–12 cm below 3DEP. GEOID09 → GEOID12B
  explains only about 1.6 cm. The rest is per-flight-line offset, which the correction model removes. *Verified*
  (Monroe, Carroll).

### 1.2 GPS time and point_source_id

- **GPS time encoding differs between deliveries.**
  - Monroe stores GPS week seconds (e.g. 147078–147501) with no week.
  - Carroll and Brown store adjusted standard time with real dates. Carroll is late March 2011; Brown is 2011-03-19.
  - *Verified.*
- **point_source_id is not a flight line in general.**
  - In Monroe, 344/345/364/365 are spatial sub-tiles.
  - In Carroll it is the flight line.
  - Flight passes are therefore split by GPS-time gaps of more than 5 s. *Verified.*

### 1.3 Points

- **Density.** Ground density is 0.36–0.60 pts/m² in 2011, against 1.6–3.1 in 3DEP.
- **Class 12.** It marks overlap points, which are up to half of a file's points. Class-12 points within 0.3 m of
  the ground surface are usable as ground. *Verified.*
- **Noise.** 2011 point noise on hard ground is about 7 cm, against 1.7 cm for 3DEP. The median of each patch is
  stable to about 3 cm. *Verified* on tile 31101425.

### 1.4 How the data is organised

*Verified* 2026-11-05 from the OT tile index and the bucket listing.

- **Size.** 44,675 LAZ files, 0.51 TB.
- **Folders.** There are 50 county folders and 2 multi-county batches. `East_IN/IN_E_2012` is flat, and
  `West_IN/IN_W_2013` has 30 county subfolders. Together they cover all 92 counties.
- **Duplicate names.** 2,152 file names occur in more than one folder. In 517 of them the copies differ in byte size
  but hold the same points; checked on Allen/Huntington `04152060`: 3,044,328 points, 100% identical.
- **Tile codes are not unique.** 2,816 codes occur in more than one folder, and the East and West State Plane grids
  reuse numbers. Use `file_id` (zone + file name), never the tile code alone.
- **County lines.**
  - Files are clipped at county lines, and 855 files (1.9%) have a side shorter than 1,400 m.
  - At the Monroe/Brown line, the Monroe half-tiles (about 730 m wide) and the Brown tile overlap by about 500 m.
  - The overlapping points come from two different flights: no shared GPS time, and nearest neighbours 0.3 m apart.
  - These overlaps give tie observations between blocks.

### 1.5 Copies of the legacy data in the public EPT bucket

The 2011–13 survey is also in the USGS public EPT bucket: `IN_Statewide-<County>Co_2011`, `USGS_LPC_IN_ET_*_2012`,
`USGS_LPC_IN_WT_*_2013`, `USGS_LPC_IN_Monroe_2011_LAS_2016` and others. These are the same points as the OT files,
re-hosted unchanged. This was *verified* 2026-11-05 on one tile each of Carroll and Monroe
(`runs/2026-11-05_ept2011_check`):

- GPS times are identical. Monroe is in week seconds there too.
- Points paired by GPS time differ by 0.000 m horizontally (4–5 mm scatter from rounding) and 0.000 m vertically
  (feet to metres).
- Only 56–65% of points keep the same class code, probably class 12 recoded on conversion to LAS 1.4. This was not
  checked.

So USGS kept OT's lon/lat as they were. The EPT copies cannot settle the datum question; ilcd's own test does. They
must never be used as the 3DEP reference (design.md section 6.1). They could serve as a second source of legacy
points cut by area.

## 2. Flight lines

- **Linking rule.** Passes are linked when:
  - their headings are within 20°;
  - the later pass starts within 900 s;
  - the along-track distance equals speed × time within 300 m;
  - the cross-track offset is under 1500 m.

  The pass centre is the midpoint of its x/y extent. The docstring of the old `link_lines.py` wrongly says
  "time-weighted".
- **The linking rule matches point_source_id** (test of 2026-11-04, `runs/2026-11-04_nadir_links`):
  - Carroll: 31 lines, identical to point_source_id (purity 1.00 both ways).
  - East Fork: 11 lines against 10 (purity 1.00 / 0.99).
- **The lines used by the old fits were over-split.**
  - The old `physlines.json` (2026-10-23) had 37 lines in Carroll and 16 in East Fork.
  - Over-splitting is safer than merging, but it adds parameters.
  - Step 2 of the refactor re-establishes the Carroll baseline on the 31 true lines.
- **Near-nadir ground tracks are worse for linking** (*verified*):
  - Nadir tracks give 47 lines in Carroll; scan-angle-projected tracks give 78.
  - 45% of passes have no near-nadir points.
  - Even adjacent same-line tracks differ across track by 35–80 m (median) because of heading error.
  - Different lines are already separated by heading, time and along-track distance. So the cross-track test only
    has to be loose enough not to cut a line.
- **Flying height**, estimated from cross-track offset ≈ H tan(scan): about 2.3 km in Carroll and 1.7 km in Monroe.
  It is kept as a diagnostic.

## 3. The correction model

- **Stage 1** (*verified* county-wide, `runs/2026-10-23_carroll_nad83`, `runs/2026-10-23_monroe_full`):
  - **Terms:** one offset per physical line, one along-track trend per county/block, and one 8-knot cubic B-spline in
    scan angle per block.
  - **CV RMS** (folds of tiles × passes):
    - Carroll: 3.88 → 3.55 cm;
    - Monroe: 4.71 → 3.93 cm.
  - **Absorption:** it absorbs at most 4% of a 1 km synthetic change.
  - **What does not work:**
    - per-line trends, per-line roll and per-direction curves overfit or do not help;
    - the tan/tan² scan model does not fit Monroe's S-shaped curve;
    - Carroll's curve differs from Monroe's, so the spline is fitted per block.
- **Scan-angle error in Carroll:** about −2.4 cm at −10° and +5.6 cm at +18°. It is the same for east- and
  west-bound passes, so it is a sensor-frame error. *Verified.*
- **Stage 2:** a per-line smooth field with 1 km knots, fitted on OSM road patches only.
  - CV RMS: Carroll 3.26–3.47 cm, Monroe 3.10 cm.
  - It absorbs at most 7% of a 200 m change.
  - With weak smoothing a field absorbs more than 50% of a 500 m change placed on its own fitting patches, so the
    smoothness weight matters. *Verified.*
- **Horizontal shift per line** (East Fork v3d): a forward/left shift is fitted on ground steeper than 10°.
  - Lines with fewer than 1,000 steep points use their area's mean shift. One line with only 12 steep points had
    produced a −5.5 m shift.
  - Results: residual ≤ 0.09 m horizontal, stable-ground NMAD 5.2 cm. *Verified.*
- **Real change hides inside the reference set.** Quarries and I-69 earthworks (Monroe 30751390 +2.1 m, 30851390
  −1.2 m) pass the patch change flags and raise the RMS from 4 to 12 cm. Areas whose median is more than 0.30 m from
  their neighbours are dropped. *Verified.*
- **Do not draw model conclusions from sparse tile samples.** A 37-tile Monroe sample suggested that stage 2 was
  useless and that stage 1 absorbed 58% at 1 km. Both were sampling artefacts. *Verified.*
- **Roofs.** Roofs of 100 m² or more, with a 2 m edge erosion, give stable vertical offsets. Smaller roofs mix ridge
  sides. The horizontal roof residual is not stable. Large roofs are rare outside towns, about one per tile.
  *Observed* (Monroe).

## 4. Change detection (East Fork, `runs/2026-10-29_eastfork_h5`)

- **Compare surfaces, not points, when densities differ.**
  - Bare cloud-to-cloud (C2C) has a background of 0.53–0.59 m.
  - Any surface method is at about 4 cm: C2C to a local plane, DoD, cloud-to-mesh, or M3C2.
  - M3C2 adds a sign, a per-point LoD, and measurement along the normal on steep banks.
  - On slopes over 30°, M3C2 is least noisy (11 cm against 13–14) but leaves 24% of points without a value.
  - *Verified* on 10 sites.
- **M3C2 + DBSCAN found 325 change objects at 10 sites.** The settings were:
  - |d| > LoD and |d| ≥ 0.3 m;
  - DBSCAN with eps 2.5 m and min_samples 10;
  - objects of at least 25 m².

  Bank retreat medians are −0.4 to −0.7 m, and −1.3 m at D5. The objects were checked visually but are not yet
  expert-labelled.
- **Floodplain rise.** The floodplain is raised by +10–15 cm near the river and +5–7 cm beyond 200 m. This may be
  deposition, but the far value is the size of the alignment error. It is open; Doug was asked.
- **Unsupervised deep clustering (DC3DCD) does not find change here.**
  - With all classes, it separates tree canopy.
  - With ground only, it separates terrain types.
  - *Verified*, so it was stopped.
- **HKCD urban benchmark** (3-class protocol of the PGN3DCD code, test tiles 12B and 12C; *verified*):

  | Method | mIoU |
  |---|---|
  | M3C2 | 45.2 |
  | M3C2 + DBSCAN | 56.6 |
  | C2C (threshold 1 m) | 59.8 |
  | PGN3DCD, our reproduction | 76.3 (paper: 77.0) |
  | SRGCD, as reported | 78.8 |

  An earlier 2-class comparison overstated C2C and was withdrawn.
- **PGN3DCD zero-shot on river banks** (HKCD weights): only 23% of bank objects are detected. This is a negative
  control: a model trained on urban data does not transfer. *Verified.*
- **Hence the method must fit the change type, data density and label availability.** Here that means surface-based
  geometry plus expert labels.

## 5. Infrastructure lessons

- **rockyweb.usgs.gov** rate-limits and then blocks the Big Red IP (HTTP 429). The public EPT (`usgs-lidar-public`)
  is reprojected with the same null datum shift, matches the original LAZ to 0.6 cm or better, and has identical
  classes. Use EPT. *Verified.*
- **Planetary Computer (NAIP)** also returns 429 above about 24 parallel jobs. Back off and cap the concurrency.
- **Missing water class.** Some 3DEP tiles have no class 9 (water). Treat 2 m cells with no 3DEP ground within 4 m
  as water.
- **Old-code crashes.** The old pipeline crashed on cells with no reference surfaces: seven Monroe forest and lake
  tiles, and four tiles clipped at the county line. In ilcd, empty is not an error.
- **Conda environments.** py4dgeo upgrades numpy and pandas, so M3C2 had its own environment (ilcd installs
  py4dgeo 1.2.1 into the main environment without changing numpy). The deep-learning
  stack (torch 1.8.1 + CUDA 11.1) is pinned separately.

## 6. Where the exploratory results are

| What | Where |
|---|---|
| Old code (read-only archive) | `legacy_code/indiana-lidar_2026-11-05.tar.gz` |
| Monroe / Carroll full-county stage-1/2 runs | `runs/2026-10-23_monroe_full`, `runs/2026-10-23_carroll_nad83` |
| East Fork v3d alignment, M3C2, objects, pages | `runs/2026-10-29_eastfork_h5` |
| HKCD, PGN3DCD reproduction and zero-shot | `runs/2026-10-30_hkcd_m3c2`, `runs/2026-10-30_pgn3dcd_repro`, `runs/2026-10-31_pgn3dcd_zeroshot_banks` |
| Flight-line linking test | `runs/2026-11-04_nadir_links` |
| OT tile index, bucket listing, probes | `index/ot_2011_tileindex/` |
| Raw downloads of Monroe, Carroll and the East Fork box | `raw/`. data_src may seed its cache from these after checking sha256. |
