# Acceptance record

The checks of docs/design.md section 11, with their results. Paths are under `$ILCD_DATA_ROOT` unless they start
with `runs/`, which is the exploration's directory `/N/project/yanlab_space/indiana_lidar/runs/`.

## Refactor step 2: Carroll, region `carroll_acceptance` (2026-11-05)

Commit: see `git log` of this file. Baseline: the exploration code (archive `indiana-lidar_2026-11-05`) re-run on
the 31 lines its current linking rule gives (`runs/2026-11-05_carroll_baseline31`).

| Check | Criterion | Result |
|---|---|---|
| Datum | W_Carroll decided `nad83` | `nad83`. Three or more informative samples per delivery; the 5 neighbouring deliveries are also `nad83` |
| Passes | Same passes as the exploration | 188 files compared: identical pass counts (491) and start times; headings differ by 0.05 deg (UTM vs State Plane grid convergence) |
| Lines | Purity 1.00 both ways against point_source_id | 31 lines, 1 block, purity 1.0000 / 1.0000 |
| Port of align + eval | The exploration's own observations reproduce its CV exactly | M3 3.92 cm, S_8 3.56 cm, narrow NMAD 5.45 / 5.03 cm, 12,290 segments: identical (`runs/2026-11-05_carroll_baseline31/paired`) |
| Observations | Stage-1 CV RMS of ilcd's observations no higher than the exploration's, under the same metric (1 km cell folds and segments) | ilcd 3.87 cm, exploration 4.11 cm; line offsets only: 4.33 cm against 4.53 cm |

Cross-validation of ilcd under other units, for reference. None of them is the exploration's metric:

| Folds / segments | line offsets | stage 1 | Note |
|---|---|---|---|
| cell / cell (default) | 4.33 cm | 3.87 cm | Leak-free |
| file / file | 3.70 cm | 3.26 cm | Leaks: a patch crossing a file boundary is seen by passes of both files |
| patch home file / patch home file | 4.42 cm | 4.07 cm | Leak-free, but 32,340 segments against the exploration's 12,290 |

Observation counts: 192,475 used (7,725 screened out); the exploration used 177,171.

## Refactor step 2, second half: stage 2, horizontal shift, batch offsets (2026-11-06)

New in this round:
- OSM road labels and water screening (rivers, streams, water bodies; drains and canals excluded);
- sloped natural ground and the per-line horizontal shift;
- one vertical offset per 3DEP batch;
- the stage-2 field and the injection test.

### Port checks (the exploration's observations through ilcd)

| Check | Exploration | ilcd |
|---|---|---|
| fit_tradeoff "road" set, CV_REPEATS=4: stage 1 / H1 lam100 / H1 lam1000 | 3.69 / 3.27 / 3.47 cm | 3.69 / 3.27 / 3.47 cm |
| absorbed share, radii 100/200/500/1000 m (stage 1; lam100; lam1000) | 0.01/0.02/0.07/0.08; 0.04/0.07/0.26/0.31; 0.04/0.06/0.15/0.24 | identical |

Target run: `runs/2026-11-05_carroll_baseline31/tradeoff_31.json`. ilcd run: `.../paired/stage2_new.json`.

### Same-metric comparison (1 km cell folds and segments, production configuration)

Production configuration:
- stage 1 fitted on non-sloped patches;
- sloped ground joins when the horizontal shift is fitted;
- stage 2 fitted on OSM-road patches, 1 km knots, lam 1000.

| Stage | Exploration observations | ilcd observations | ilcd sloped-ground NMAD |
|---|---|---|---|
| line offsets | 4.53 cm | 4.34 cm | 9.27 cm |
| stage 1 | 4.11 cm | 3.87 cm | 8.91 cm |
| stage 1 + horizontal shift | — | 3.86 cm | 8.37 cm |
| stage 2 | 3.93 cm | 3.71 cm | 8.26 cm |

Injection (+/- 10 cm at 30 centres, all observations as targets), median absorbed share:

| Radius | expl. stage 1 | expl. stage 2 | ilcd stage 1 | ilcd stage 1 + shift | ilcd stage 2 |
|---|---|---|---|---|---|
| 200 m | 0.5% | 0.7% | 0.5% | 0.7% | 0.2% |
| 500 m | 2.0% | 4.2% | 3.5% | 4.6% | 4.6% |
| 1000 m | 4.6% | 12.1% | 5.5% | 5.7% | 9.3% |

ilcd's observations do no worse at every stage, so the check passes.

### Model facts (carroll_acceptance)

- **Observations.** 259,649 used. 90,300 were screened out:
  - 50,021 within 100 m of rivers, streams or lakes, mostly sloped ground in the Wabash, Tippecanoe and Deer Creek
    valleys;
  - 7,503 change suspects;
  - 3 gross cells.
- **3DEP batches.** Opt2_B2 is the reference. Opt1_B1 is +0.3 cm and Opt1_B2 is −1.0 cm; both only touch the
  county's edges.
- **Horizontal shifts.** Most lines lie within ±0.15 m. One line, abs@-14708091.9 (2011-03-27 20:11), is shifted
  by (left +0.98, forward −0.69) m. Its 2,115 sloped observations, which face every direction, have an NMAD of
  8.6 cm with the shift and 28.3 cm without it. So the shift is real. The exploration's Carroll model was vertical
  only and left this error in place; slopes under that line would have shown false change.
- **Block terms.** Along-track trend −0.09 cm/km; scan-angle slope of the shift 0.3 cm per degree.
