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

**Resolved: horizontal shift on hard ground (2026-11-06).** In Monroe the shift raised the hard-ground segment RMS
(4.84 -> 5.00 cm). The threshold was not the cause: CV is the same for `hshift_min_obs` 30, 100, 300 and 1000
(nearly every line has more sloped observations; p10 per line 312 in Monroe, 109 in Bartholomew), and pooling every
line is worse (5.05 cm). The cause was the sloped-ground observations themselves: they joined the whole stage-1 fit
and, sitting 1-4 cm off flat ground after correction, pulled the line offsets. A nuisance offset for sloped rows
(`align.stage1.slope_offset`, per block) fixes it:

| Sloped-ground offset | Monroe stage 1 + shift | Monroe stage 2 | Bartholomew stage 1 + shift | Bartholomew stage 2 |
|---|---|---|---|---|
| none (before) | 5.00 cm / slope 6.78 | 3.93 cm | 5.40 cm / slope 7.05 | 3.75 cm |
| per block (default) | 4.75 cm / slope 6.75 | 3.92 cm | 5.39 cm / slope 7.04 | 3.75 cm |
| per line | 4.74 cm / slope 6.74 | 3.90 cm | 5.38 cm / slope 7.03 | 3.75 cm |

Fitted offsets: Monroe +0.9 to +2.4 cm, Bartholomew -3.9 to +2.1 cm per block. Per block is the default: per line is
0.01 cm better for a hundred more parameters, and the departure comes from ground cover, not from a line.

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

## Refactor step 4: Bartholomew, Jackson and the cross-county scope (2026-11-06)

Runs: the chunked workflow, `upper` target, per county (controllers 8849847, 8849848) and for the cross-county
region `bartholomew_jackson` (8850254), then `change` for site `eastfork_doug` (section below). The county and
cross-county runs predate `align.stage1.slope_offset` (a2f153d); they are re-run with it before the release.

### Correction, by region (1 km cells, default test set; slope = sloped-ground NMAD)

| Region | Lines | Blocks | Obs fitted | line offsets | stage 1 | stage 1 + shift | stage 2 | slope (stage 2) | absorbed at 1 km (stage 2) |
|---|---|---|---|---|---|---|---|---|---|
| bartholomew | 80 | 5 | 596,659 | 6.14 cm | 5.41 cm | 5.40 cm | 3.75 cm | 6.50 cm | 17.3% |
| jackson | 123 | 3 | 781,827 | 7.18 cm | 6.93 cm | 6.96 cm | 4.71 cm | 7.34 cm | 9.1% |
| bartholomew_jackson | 174 | 5 | 1,333,840 | 6.81 cm | 6.42 cm | 6.43 cm | 4.28 cm | 7.08 cm | 11.3% |

- Datum: every delivery read here is nad83 (E_Bartholomew, E_Jackson, E_IN_E_2012, W_IN_W_2013-Greene, -Owen).
  The first datum job of E_IN_E_2012 failed and succeeded on retry.
- Cross-county linking works: the two counties alone have 203 lines, the joint scope 174, so 29 lines cross the
  county line.
- 3DEP batch offsets: Bartholomew -1.3 cm (Opt1_B3); Jackson +1.5 to +2.1 cm (Opt1_B5, Opt2_B4, Opt2_B5) relative
  to Opt1_B4.
- Jackson's errors are the largest so far, and stage 2 removes most of them (6.96 -> 4.71 cm): its errors vary along
  the lines more than elsewhere.
- Status of the joint scope: 4,560 (cell, line) pairs corrected, 982 not corrected, 896 without data, 3 stage 1
  only; median LoD95 7.7 cm (Monroe 7.3 cm).

### Same-metric comparison on the East Fork box (site eastfork_doug)

The exploration fitted the box alone (49 tiles, 14,308 observations, 15 lines; its own CV with tile folds: stage 1
6.82 cm, stage 2 3.76 cm with stage 2 fitted on all hard ground). Here both observation sets are fitted on the box
cells only and scored with 1 km cell folds (`runs/2026-10-23_monroe_full/paired/`-style scripts, logs
`efsame_*`, `efcheck_*`):

| Test set | Stage | Exploration observations | ilcd observations |
|---|---|---|---|
| default (narrow hard ground) | stage 1 | 6.97 cm | 7.31 cm |
| default (narrow hard ground) | stage 2 | 5.38 cm | 5.75 cm |
| OSM roads (same definition for both) | stage 1 | 6.54 cm | 6.14 cm |
| OSM roads (same definition for both) | stage 2 | 4.27 cm | 4.16 cm |

On a test set both observation sets define the same way, ilcd's observations do better at both stages, as in Monroe.
The default test sets differ: ilcd's includes NDVI hard ground on the floodplain, much of it bare cropland that is
tilled between the epochs, which raises the score without the correction being worse. In farm counties, compare
on roads.

What the site actually uses is the joint model, fitted on all 1.33 million observations of both counties. Its
cross-validated residuals in the box cells (3 repeats):

| Test set | stage 1 | stage 2 |
|---|---|---|
| OSM roads, box cells | 6.65 cm | 4.14 cm |
| narrow hard ground, box cells | 7.32 cm | 4.29 cm |
| OSM roads, whole region | 6.12 cm | 3.84 cm |
| narrow hard ground, whole region | 6.41 cm | 4.18 cm |

A box alone (12-15 lines) constrains the lines poorly: the same narrow test set scores 5.75 cm fitted on the box and
4.29 cm in the joint model, where the box lines borrow observations from outside.

### DoD on real data (Monroe, 4 cells)

The DoD runs in 1-1.5 min per cell. Two findings, both in design.md section 7.1:
- A 2 m pixel holds 3-4 2011 ground points; their own SD is unstable and set the LoD too low. The point SD is now
  pooled over 3 x 3 pixels (`change.dod.sd_window_px`): "lowered" pixels drop by 10-28%.
- 7-9% of pixels remain "lowered" against 1-3% "raised", also on roads (dz -1.4 cm, NMAD 3.8 cm). Pixel medians
  instead of means change nothing, so it is not above-ground contamination. A 95% per-pixel test flags about 5% of
  unchanged pixels, and a local residual offset of the correction (the spread of status bias is about 3 cm) moves
  them to one side. The change product is therefore the object map, with area and magnitude limits.

### Change objects on site eastfork_doug, against the exploration's 325 objects

The site run (`jobs/snakemake.sh eastfork_doug change`) gives 3,256 objects over the box: 1,683 gain (315,721 m²,
+202,827 m³) and 1,573 loss (191,896 m², -145,036 m³); median area 50 m², largest 7,490 m². The exploration
(runs/2026-10-29_eastfork_h5) found 325 objects in 10 windows of 400 m chosen at change hot spots. Check
(`compare_old_objects.py`, output `sites/eastfork_doug/change/compare_old.json`): an old object counts as found when
its centroid lies within 5 m of a new object of the same kind.

| Window | Old | Found | New | Old loss m³ | New loss m³ | Old gain m³ | New gain m³ |
|---|---|---|---|---|---|---|---|
| D1 | 22 | 21 | 23 | -654 | -682 | 4,419 | 4,311 |
| D2 | 19 | 18 | 22 | -7,556 | -8,083 | 1,707 | 2,354 |
| D3 | 15 | 15 | 14 | -436 | -431 | 5,027 | 5,108 |
| D4 | 10 | 9 | 8 | -6,156 | -5,903 | 5,899 | 6,158 |
| D5 | 38 | 37 | 44 | -10,079 | -10,574 | 11,728 | 15,261 |
| B1 | 51 | 39 | 57 | -5,074 | -6,058 | 3,898 | 3,274 |
| B2 | 41 | 35 | 40 | -5,793 | -7,972 | 7,442 | 8,751 |
| B3 | 50 | 47 | 51 | -6,526 | -6,875 | 10,812 | 18,419 |
| B4 | 48 | 48 | 53 | -3,894 | -3,928 | 4,907 | 5,920 |
| B5 | 31 | 31 | 40 | -1,515 | -2,747 | 2,990 | 5,761 |
| All | 325 | 300 | 352 | -47,683 | -53,253 | 58,829 | 75,317 |

- 300 of 325 old objects are found, 97% of their area: all 106 of 100-1,000 m², 22 of 23 above 1,000 m², 172 of
  196 of 25-100 m². The misses are small objects near the area and magnitude limits; half of them are in B1.
- New volumes are those of whole objects touching a window, not clipped to it, so they run higher where large objects
  cross the window edge (B3, B5, D5). Inside windows without such objects the volumes agree within 10%.
- Accepted: the ported change chain reproduces the exploration's objects; the differences come from the joint
  correction (174 lines, 1 km stage 2) replacing the exploration's box-only fit.
