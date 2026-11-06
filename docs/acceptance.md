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

## Refactor step 2, flat natural ground and CDL (2026-11-06)

New:
- flat natural ground (natural_flat, also under canopy);
- USDA CDL cropland mask;
- change screening within each surface family.

### CDL ablation (Carroll, same observations, OSM roads as a fixed test set)

Excluding cropland "hard ground" and flat ground removed a third of the observations and made the correction worse
on roads. The test set is the same in both runs, since CDL never removes roads:

| Stage | Cropland excluded | Cropland kept |
|---|---|---|
| stage 1 | 4.50 cm | 4.14 cm |
| stage 2 | 3.91 cm | 3.87 cm |

Flat natural ground in the stage-1 fit made no difference on roads (4.15 vs 4.14 cm). (The narrow-patch numbers
first given here scored flat natural ground too; see the correction below.)

Decision: no surface kind is excluded on cropland by default (`refs.screen.cdl_kinds: []`). The change screen
already drops patches that depart from their neighbours, which catches tilled fields. CDL stays available, and is
needed later by change detection, where tillage produces false change.

Ablation runs:
- `ilcd_ablation_nocdl/` (separate data root, cells only);
- `runs/2026-11-05_carroll_baseline31/paired/cv_variants*.out`.

### Carroll with the final defaults (same metric as above: 1 km cells)

Corrected on 2026-11-06 (f22e87e). The first version of this table scored narrow flat natural ground as if it were
hard ground: narrowness is a shape test made for every patch, and the CV only excluded sloped ground. Flat natural
ground was 31% of the scored observations here (58% in Monroe). The test set is now narrow patches of
`eval.cv.test_kinds` (road, hard_other).

| Stage | Exploration observations | ilcd, first version | ilcd, final defaults | sloped-ground NMAD (final) |
|---|---|---|---|---|
| line offsets | 4.53 cm | 4.34 cm | 4.44 cm | 9.26 cm |
| stage 1 | 4.11 cm | 3.87 cm | 3.98 cm | 8.89 cm |
| stage 1 + horizontal shift | — | 3.86 cm | 3.96 cm | 8.37 cm |
| stage 2 | 3.93 cm | 3.71 cm | 3.84 cm | 8.26 cm |

The check passes: no worse than the exploration at any stage (46,340 segments).

Observations: 318,386 used; 116,349 were screened out (water, change suspects).

Injection, median absorbed share:

| Radius | expl. stage 1 | expl. stage 2 | ilcd stage 1 | ilcd stage 2 |
|---|---|---|---|---|
| 200 m | 0.5% | 0.7% | 0.8% | 1.1% |
| 500 m | 2.0% | 4.2% | 4.4% | 7.7% |
| 1000 m | 4.6% | 12.1% | 10.7% | 17.3% |

**Open issue.** The denser observations make 1 km changes easier to absorb: the line offsets follow a change that
covers many observations of one line. Changes of 200 m or less are unaffected. Before the paper, decide between
three options:
- thinning observations per line and area;
- a stronger stage-2 smoothness;
- reporting it as the method's spatial resolution limit.

**Decided (2026-11-06, Da Yan): report it as the method's spatial resolution limit.** The method is unchanged. The
paper states that changes up to a few hundred metres are kept (at most about 1% absorbed at 200 m, 3-8% at 500 m),
while a uniform change of about 1 km is underestimated by about 15% (13-17% in Carroll and Monroe). The target
changes (bank erosion and deposition) are far smaller than that. The injection test stays in eval, so the limit is
measured for every scope.

## Refactor step 3: Monroe, region `monroe` (2026-11-06)

Run: the chunked Snakemake workflow, `upper` target, 201 steps (controller job 8846815). 695 legacy files
(Monroe plus the files touching its padded cells), 1,303 cells.

### Checks

| Check | Result |
|---|---|
| Datum | W_Monroe itrf (3 informative samples, e.g. E0553N4331: itrf 0.00 m vs nad83 1.06 m); neighbouring deliveries nad83 |
| Time domains | W_Morgan stores GPS week seconds: its 10 lines are `wk:W_Morgan` and link only within W_Morgan; all others absolute |
| Lines and blocks | 111 lines in 4 seasons. 101 have observations; the other 10 are fragments of 1-178 points, or lines of neighbouring deliveries (W_Lawrence, W_Morgan, Owen, Greene) running along the padding at the north and south edges, where no cell has references |
| Forest and lake tiles (no observations in the exploration) | 31401475, 31501375, 31501380, 31551370, 31551375, 31601370, 31601380: 3 passes and 360-1,060 observations each, from flat and sloped natural ground |
| Tiles cut by the Monroe-Brown county line | 31501470, 31501480, 31551410, 31701365: 2 passes each, linked across the line to the Brown County halves (E.ot_in2011_0125xxxx, in the padding); 430-580 observations each |
| Screening | 104,196 near water, 25,248 change suspects, 458 in gross cells; 1,080,156 observations fitted |
| Batch offsets | IN_Statewide_Opt2_B4_2017 fixed at 0; IN_Statewide_Opt1_B4_2017 -3.4 cm |

### Port check (the exploration's Monroe observations through ilcd)

`runs/2026-10-23_monroe_full/paired/old_obs_monroe.py`: 146,780 observations, 47 lines, 475 tiles (two tiles with
whole-tile change excluded, as in the exploration).

| Stage | Exploration (tradeoff_full_v2.json) | ilcd, same observations, tile folds |
|---|---|---|
| stage 1 | 3.93 cm | 3.94 cm |
| stage 2 (1 km knots, lam 1000, roads) | 3.10 cm | 3.13 cm |

Stage 1 reproduces. The exploration's stage-2 run fitted stage 1 on roads only, ilcd on roads and other hard
ground, so stage 2 is close rather than identical.

### Same-metric comparison (1 km cells, test set: narrow road and hard_other patches)

| Stage | Exploration observations | ilcd observations | sloped-ground NMAD (ilcd) |
|---|---|---|---|
| line offsets | 5.71 cm | 5.52 cm | 8.58 cm |
| stage 1 | 4.89 cm | 4.84 cm | 8.43 cm |
| stage 1 + horizontal shift | — | 5.00 cm | 6.78 cm |
| stage 2 | 3.96 cm | 3.93 cm | 6.58 cm |

The check passes, narrowly at stages 1 and 2 (49,080 vs 59,320 segments; the test sets differ). On a test set both
observation sets define the same way, patches at least half on OSM roads:

| Stage | Exploration observations | ilcd observations |
|---|---|---|
| stage 1 | 4.83 cm | 4.96 cm |
| stage 2 | 3.76 cm | 3.69 cm |

**Open issue: horizontal shift on hard ground.** In Monroe the horizontal shift raises the hard-ground segment
RMS (4.84 -> 5.00 cm) while it lowers the narrow NMAD (4.89 -> 4.66 cm) and the sloped-ground NMAD (8.43 -> 6.78 cm).
In Carroll it is neutral on hard ground. Stage 2 removes the difference in both regions. To look at with the
provisional `hshift_min_obs` (30): the shift is fitted on sloped ground, and a line with few sloped observations in
forest may get a shift that hard ground does not support.

Injection, median absorbed share (stage 2): 0.4% at 200 m, 3.4% at 500 m, 13.2% at 1000 m.

### Stage 2 fitted on more surface kinds (decision: keep roads)

Fitting the stage-2 field on more kinds lowers the CV error and raises the absorption of real change. Same
observations, default CV; the road test set is unaffected by which kinds are fitted.

| Stage-2 fit kinds | Carroll, hard-ground test | Carroll, road test | Monroe, hard-ground test | Monroe, road test |
|---|---|---|---|---|
| road (default) | 3.84 cm | 3.91 cm | 3.93 cm | 3.66 cm |
| road + hard_other | 3.37 cm | 3.56 cm | 3.75 cm | 3.62 cm |
| road + hard_other + natural_flat | 3.32 cm | 3.50 cm | 3.81 cm | 3.72 cm |

Injection, median absorbed share at 500 m / 1000 m:

| Stage-2 fit kinds | Carroll | Monroe |
|---|---|---|
| road (default) | 7.7% / 17.3% | 3.4% / 13.2% |
| road + hard_other | 14.6% / 26.2% | 5.0% / 20.8% |
| road + hard_other + natural_flat | 13.5% / 28.3% | 9.0% / 24.4% |

Decision: `align.stage2.fit_kinds` stays `[road]`. The product is change detection, and the extra kinds buy at
most 0.41 cm on roads in Carroll and 0.04 cm in Monroe, where flat natural ground even makes roads worse, for half
again as much absorbed change at 1 km. (The injection issue above is decided: the limit is reported.) Runs: `runs/2026-10-23_monroe_full/paired/` (s2fix_*, s2road_*, inj_*).
